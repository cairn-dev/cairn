#!/usr/bin/env python3

import curses
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from curses import wrapper as curses_wrapper
from typing import List, Optional, Tuple
from datetime import datetime

from dotenv import load_dotenv

# Add the cairn_utils directory to the Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'cairn_utils'))

from cairn_utils.task_storage import TaskStorage

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # Change to DEBUG level
handlers = [
    logging.FileHandler('debug.log'),
    logging.StreamHandler()
]
for handler in handlers:
    handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(handler)

# Setup subprocess logger
subprocess_logger = logging.getLogger('subprocess_logger')
subprocess_logger.setLevel(logging.INFO)

# Create logs/subprocesses directory if it doesn't exist
os.makedirs('logs/subprocesses', exist_ok=True)

# Create handler for subprocess logger
subprocess_handler = logging.FileHandler('logs/subprocesses/subprocess.log')
subprocess_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
subprocess_logger.addHandler(subprocess_handler)

# Load environment variables
load_dotenv('.env')
logger.info("Environment variables loaded from .env")
logger.info(f"CONNECTED_REPOS from env: {os.getenv('CONNECTED_REPOS')}")

class WorkerManager:
    def __init__(self):
        logger.info("Initializing WorkerManager")
        self.selected_agent = 0  # Initialize to 0 instead of None
        self.selected_repos = []  # List of (owner, repo) tuples
        self.task_description = ""
        self.current_screen = "main"
        self.cursor_pos = 0
        self.selected_repo_idx = 0
        self.active_tasks = {}
        self.selected_task_id = None  # Track selected task for viewing logs
        self.selected_task_idx = 0  # Track selected task index in the list
        self.log_scroll_pos = 0  # Track log scroll position
        self.running_tasks = {}  # Track worker processes instead of asyncio tasks
        self.task_storage = TaskStorage()

        # Load and parse repos.json
        try:
            with open("repos.json", "r") as f:
                repo_data = json.load(f).get("connected_repos", [])
                self.connected_repos = self._parse_connected_repos(repo_data)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"Could not load or parse repos.json: {e}")
            self.connected_repos = []

        logger.info(f"Parsed repositories: {self.connected_repos}")

        # Get owner from first repo (assuming all repos have same owner)
        self.owner = self.connected_repos[0][0] if self.connected_repos else "unknown"
        logger.info(f"Owner set to: {self.owner}")

        logger.info("Worker manager initialized")

    def add_debug_message(self, message: str):
        """Add a debug message to display in UI"""
        timestamp = time.strftime("%H:%M:%S")
        formatted_message = f"[{timestamp}] {message}"
        self.task_storage.add_debug_message(formatted_message)
        logger.debug(message)

    def _parse_connected_repos(self, repo_data: List[dict]) -> List[Tuple[str, str]]:
        """Parse data from repos.json into a list of (owner, repo) tuples"""
        if not repo_data:
            print("Warning: connected_repos in repos.json is empty or not found.")
            return []

        repos = []
        for repo_info in repo_data:
            owner = repo_info.get("owner")
            repo = repo_info.get("name")
            if owner and repo:
                print(f"Found repository: {owner}/{repo}")
                repos.append((owner, repo))
            else:
                print(f"Warning: Invalid repository format in repos.json: {repo_info}")

        if not repos:
            print("Warning: No valid repositories found in repos.json")
        else:
            print(f"Successfully parsed {len(repos)} repositories")

        return repos

    def log_worker_output(self, pipe, task_id: str, prefix: str):
        """Log output from a worker process"""
        try:
            for line in iter(pipe.readline, ""):
                if line:
                    logger.info(f"[{task_id}] {prefix}: {line.strip()}")
                else:
                    # Empty line might indicate pipe is closed
                    break
        except (BrokenPipeError, OSError, ValueError) as e:
            # Handle pipe closed during cleanup
            logger.debug(f"Pipe closed for {task_id} {prefix}: {str(e)}")
        except Exception as e:
            logger.error(f"Error reading worker output for {task_id}: {str(e)}")

    def run_worker_process(self, task_id: str) -> subprocess.Popen:
        """Run a single worker process for a task"""
        try:
            print(f"[DEBUG] Manager creating worker process for task {task_id}")

            # Set up environment for worker
            env = os.environ.copy()

            # Start the worker process using the Python module approach for better reliability
            print(f"[DEBUG] Manager starting subprocess for task {task_id}")
            process = subprocess.Popen(
                [sys.executable, "-m", "agent_worker", task_id],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                bufsize=1,
                preexec_fn=os.setsid if hasattr(os, 'setsid') else None,  # Create process group on Unix
            )

            # Log subprocess creation
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            subprocess_logger.info(f"Subprocess started: PID={process.pid}, task_id={task_id}, command=[{sys.executable}, -m, agent_worker, {task_id}]")

            print(f"[DEBUG] Manager created process PID {process.pid} for task {task_id}")

            # Start threads to log output (store thread references for cleanup)
            stdout_thread = threading.Thread(
                target=self.log_worker_output,
                args=(process.stdout, task_id, "stdout"),
                daemon=False  # Don't use daemon threads for proper cleanup
            )
            stderr_thread = threading.Thread(
                target=self.log_worker_output,
                args=(process.stderr, task_id, "stderr"),
                daemon=False
            )

            stdout_thread.start()
            stderr_thread.start()

            # Store thread references for cleanup
            if not hasattr(self, 'worker_threads'):
                self.worker_threads = {}
            self.worker_threads[task_id] = {
                'stdout_thread': stdout_thread,
                'stderr_thread': stderr_thread
            }

            logger.info(f"Started worker process for task {task_id}")
            return process

        except Exception as e:
            logger.error(f"Error starting worker process for task {task_id}: {str(e)}")
            # Log subprocess error
            subprocess_logger.error(f"Failed to start subprocess for task {task_id}: {str(e)}")
            raise

    def create_task_sync(self, agent_type: str, description: str, repos: List[str]) -> str:
        """Create a new task synchronously and start a worker process"""
        try:
            self.add_debug_message(f"Creating task with agent_type='{agent_type}', description='{description[:50]}...', repos={repos}")

            # Generate a unique task ID
            task_id = f"task_{int(time.time())}"
            logger.debug(f"Generated task_id: {task_id}")

            # Create payload based on agent type
            if agent_type == "Fullstack Planner":
                payload = {
                    "run_id": task_id,
                    "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "repos": repos,
                    "owner": self.owner,
                    "description": description,
                    "subtask_ids": [],
                    "agent_output": {},
                    "agent_status": "Queued",
                    "agent_type": agent_type,
                    "raw_logs_dump": {}
                }
            else:  # SWE or PM
                payload = {
                    "run_id": task_id,
                    "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "repo": repos[0] if repos else "",  # Single repo for SWE/PM
                    "owner": self.owner,
                    "description": description,
                    "agent_output": {},
                    "agent_status": "Queued",
                    "agent_type": agent_type,
                    "related_run_ids": [],
                    "raw_logs_dump": {},
                    "branch": None
                }

            logger.debug(f"Created payload: {payload}")

            # Create auto-persisting payload and store in database
            persistent_payload = self.task_storage.create_active_task_persistent(task_id, payload)

            # Store reference to the persistent payload
            self.active_tasks[task_id] = persistent_payload
            self.add_debug_message(f"Added task {task_id} to active_tasks")

            # Start worker process
            try:
                process = self.run_worker_process(task_id)
                self.running_tasks[task_id] = process
                self.add_debug_message(f"Started worker process for task {task_id}")
            except Exception as e:
                self.add_debug_message(f"Failed to start worker process: {str(e)}")
                persistent_payload["agent_status"] = "Failed - Process Error"
                persistent_payload["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

            logger.info(f"Created and started task {task_id} with agent type: {agent_type}")
            return task_id

        except Exception as e:
            error_msg = f"Error creating task: {str(e)}"
            logger.error(error_msg)
            self.add_debug_message(error_msg)
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return ""

    def get_task(self, task_id: str) -> dict:
        """Get a task by ID, returning auto-persisting payload if available"""
        # First try to get from active_tasks (which might have PersistentDict)
        if task_id in self.active_tasks:
            return self.active_tasks[task_id]

        # If not in memory, try to get from database as PersistentDict
        persistent_task = self.task_storage.get_active_task_persistent(task_id)
        if persistent_task:
            # Cache it in active_tasks for future access
            self.active_tasks[task_id] = persistent_task
            return persistent_task

        # Fallback to regular dict from database
        return self.task_storage.get_active_task(task_id) or {}

    def get_task_status(self, task_id: str) -> Optional[dict]:
        """Get the status of a task (backward compatibility method)"""
        return self.get_task(task_id)

    def list_tasks(self) -> list:
        """List all active tasks"""
        # Get all tasks from database (includes both in-memory and persisted)
        all_tasks = self.task_storage.get_all_active_tasks()

        # Update our in-memory cache with PersistentDict for any missing tasks
        for task_id in all_tasks:
            if task_id not in self.active_tasks:
                self.active_tasks[task_id] = self.task_storage.get_active_task_persistent(task_id)

        # Return the original payload dictionaries (they already have correct format)
        return list(all_tasks.values())

    def cleanup(self):
        """Clean up running tasks"""
        try:
            logger.info("Starting cleanup of running tasks...")

            # Terminate any running worker processes
            for task_id, process in list(self.running_tasks.items()):
                try:
                    if process.poll() is None:
                        logger.info(f"Terminating worker process for task {task_id} (PID: {process.pid})")

                        # Try to terminate the process group (kills child processes too)
                        try:
                            if hasattr(os, 'killpg'):
                                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                            else:
                                process.terminate()
                            # Log subprocess termination
                            subprocess_logger.info(f"Subprocess termination requested: PID={process.pid}, task_id={task_id}, signal=SIGTERM")
                        except (OSError, ProcessLookupError):
                            # Process might already be dead
                            process.terminate()
                            subprocess_logger.info(f"Subprocess termination fallback: PID={process.pid}, task_id={task_id}, signal=SIGTERM")

                        # Wait for clean termination
                        try:
                            process.wait(timeout=5)
                            logger.info(f"Cleanly terminated worker process for task {task_id}")
                            subprocess_logger.info(f"Subprocess terminated cleanly: PID={process.pid}, task_id={task_id}")
                        except subprocess.TimeoutExpired:
                            # Force kill if it doesn't terminate gracefully
                            logger.warning(f"Force killing worker process for task {task_id}")
                            subprocess_logger.warning(f"Subprocess force kill required: PID={process.pid}, task_id={task_id}")
                            try:
                                if hasattr(os, 'killpg'):
                                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                                else:
                                    process.kill()
                                process.wait(timeout=2)
                                subprocess_logger.info(f"Subprocess force killed: PID={process.pid}, task_id={task_id}, signal=SIGKILL")
                            except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
                                # Process is already dead or we can't kill it
                                subprocess_logger.warning(f"Subprocess kill failed or process already dead: PID={process.pid}, task_id={task_id}")
                                pass

                    # Close pipes to prevent resource leaks
                    try:
                        if process.stdout and not process.stdout.closed:
                            process.stdout.close()
                        if process.stderr and not process.stderr.closed:
                            process.stderr.close()
                        if process.stdin and not process.stdin.closed:
                            process.stdin.close()
                    except Exception as e:
                        logger.warning(f"Error closing pipes for task {task_id}: {e}")

                except Exception as e:
                    logger.error(f"Error cleaning up task {task_id}: {str(e)}")

            # Wait for worker threads to finish
            if hasattr(self, 'worker_threads'):
                for task_id, threads in self.worker_threads.items():
                    try:
                        logger.info(f"Waiting for worker threads for task {task_id} to finish...")
                        threads['stdout_thread'].join(timeout=3)
                        threads['stderr_thread'].join(timeout=3)
                    except Exception as e:
                        logger.warning(f"Error joining worker threads for task {task_id}: {e}")

                self.worker_threads.clear()

            # Clear running tasks
            self.running_tasks.clear()
            logger.info("Cleanup completed successfully")

        except Exception as e:
            logger.error(f"Error during cleanup: {str(e)}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")

    def draw_agent_selection_screen(self, stdscr):
        """Draw the agent selection screen"""
        height, width = stdscr.getmaxyx()

        # Title
        title = "Select Agent Type"
        stdscr.addstr(0, (width - len(title)) // 2, title, curses.A_BOLD)

        # Options
        options = [
            "1. Fullstack Planner",
            "2. PM (Project Manager)",
            "3. SWE (Software Engineer)",
            "q. Quit"
        ]

        for idx, option in enumerate(options, start=2):
            if idx - 2 == self.selected_agent:
                stdscr.addstr(idx, 2, option, curses.A_REVERSE)
            else:
                stdscr.addstr(idx, 2, option)

        # Instructions
        instructions = "Use arrow keys to select, Enter to confirm, 'q' to quit"
        stdscr.addstr(height - 1, 0, instructions[:width - 1])

    def draw_repo_selection_screen(self, stdscr):
        """Draw the repository selection screen"""
        height, width = stdscr.getmaxyx()

        # Title
        title = "Select Repository"
        stdscr.addstr(0, (width - len(title)) // 2, title, curses.A_BOLD)

        # Instructions based on agent type
        if self.selected_agent == 0:  # Fullstack Planner
            instructions = "Use arrow keys to navigate, Space to select multiple, Enter to confirm, 'b' to go back"
        else:  # PM or SWE
            instructions = "Use arrow keys to navigate, Space to select (one only), Enter to confirm, 'b' to go back"
        stdscr.addstr(2, 2, instructions[:width - 4])

        # Check if we have any repositories
        if not self.connected_repos:
            error_msg = "No repositories configured. Please set CONNECTED_REPOS in .env.local"
            stdscr.addstr(4, 2, error_msg, curses.color_pair(2))
            stdscr.addstr(6, 2, "Press 'b' to go back")
            return

        # Repository list
        for idx, (owner, repo) in enumerate(self.connected_repos, start=4):
            prefix = "[x] " if (owner, repo) in self.selected_repos else "[ ] "
            repo_str = f"{owner}/{repo}"
            if idx - 4 == self.selected_repo_idx:
                stdscr.addstr(idx, 2, f"{prefix}{repo_str}", curses.A_REVERSE)
            else:
                stdscr.addstr(idx, 2, f"{prefix}{repo_str}")

    def draw_task_description_screen(self, stdscr):
        """Draw the task description input screen"""
        height, width = stdscr.getmaxyx()

        # Title
        title = "Enter Task Description"
        stdscr.addstr(0, (width - len(title)) // 2, title, curses.A_BOLD)

        # Instructions
        instructions = "Type your task description (press Enter for new line, Enter twice quickly when done):"
        stdscr.addstr(2, 2, instructions[:width - 4])

        # Show current description with cursor
        desc_lines = self.task_description.split('\n')
        max_lines = height - 8  # Leave space for header, instructions, and footer

        # Display visible lines with wrapping
        current_display_line = 4
        wrapped_lines = []

        # Process each line of the description
        for line in desc_lines:
            # Wrap long lines
            while line:
                if current_display_line >= height - 4:  # Leave space for footer
                    break
                # Calculate how much of the line we can display
                display_length = min(len(line), width - 4)
                wrapped_lines.append(line[:display_length])
                line = line[display_length:]
                current_display_line += 1

        # Display the wrapped lines
        for i, line in enumerate(wrapped_lines):
            if i >= max_lines:
                break
            stdscr.addstr(4 + i, 2, line)

        # Calculate cursor position in wrapped text
        cursor_line = 0
        cursor_col = 0
        remaining_pos = self.cursor_pos

        # Find the correct line and column for the cursor in wrapped text
        current_pos = 0
        for i, line in enumerate(desc_lines):
            line_length = len(line)
            if remaining_pos <= current_pos + line_length:
                # Cursor is in this line
                relative_pos = remaining_pos - current_pos
                # Calculate which wrapped line contains the cursor
                wrapped_line_index = relative_pos // (width - 4)
                cursor_line = i + wrapped_line_index
                cursor_col = relative_pos % (width - 4)
                break
            current_pos += line_length + 1  # +1 for newline

        # Show cursor at the right position
        if cursor_line < max_lines:
            try:
                stdscr.move(4 + cursor_line, 2 + cursor_col)
            except curses.error:
                pass  # Ignore cursor positioning errors

        # Show selected repositories
        repo_start_line = 4 + min(len(wrapped_lines), max_lines) + 1
        stdscr.addstr(repo_start_line, 2, "Selected repositories:")
        for i, repo in enumerate(self.selected_repos):
            repo_str = f"{repo[0]}/{repo[1]}"
            if len(repo_str) > width - 6:
                repo_str = repo_str[:width - 9] + "..."
            stdscr.addstr(repo_start_line + 1 + i, 4, repo_str)

        # Draw footer
        footer = "Use arrow keys to navigate, Enter for new line, Enter twice quickly to submit"
        stdscr.addstr(height - 1, 0, footer[:width - 1])

    def draw_main_screen(self, stdscr):
        """Draw the main screen"""
        height, width = stdscr.getmaxyx()

        # Draw header
        stdscr.addstr(0, 0, "Worker Manager", curses.A_BOLD)
        stdscr.addstr(1, 0, "Press 'n' to create new task, 'd' to delete task, 'q' to quit")

        # Draw debug messages
        debug_start_line = 2
        stdscr.addstr(debug_start_line, 0, "Debug Messages:", curses.A_UNDERLINE)
        debug_messages = self.task_storage.get_debug_messages(5)  # Get last 5 debug messages
        for i, msg in enumerate(debug_messages):
            if debug_start_line + 1 + i < height - 5:  # Leave space for tasks and footer
                stdscr.addstr(debug_start_line + 1 + i, 0, msg[:width - 1])

        # Draw task list
        task_start_line = debug_start_line + 7
        tasks = self.list_tasks()
        if not tasks:
            stdscr.addstr(task_start_line, 0, "No tasks found")
        else:
            stdscr.addstr(task_start_line - 1, 0, "Active Tasks:", curses.A_UNDERLINE)
            current_line = task_start_line
            for i, task in enumerate(tasks):
                if current_line >= height - 2:  # Don't draw beyond screen
                    break

                # Draw task selection indicator
                if i == self.selected_task_idx:
                    stdscr.addstr(current_line, 0, "> ", curses.A_BOLD)
                else:
                    stdscr.addstr(current_line, 0, "  ")

                # Draw task info
                status = task.get("agent_status", "unknown")
                status_color = curses.A_NORMAL
                if status == "Completed":
                    status_color = curses.A_BOLD | curses.color_pair(1)  # Green
                elif status == "Failed":
                    status_color = curses.A_BOLD | curses.color_pair(2)  # Red
                elif status == "Running":
                    status_color = curses.A_BOLD | curses.color_pair(3)  # Yellow

                # Get PR URL if available
                pr_url = None
                if task.get("agent_output", {}).get("pull_request_url"):
                    pr_url = task["agent_output"]["pull_request_url"]
                elif task.get("agent_output", {}).get("pr_url"):
                    pr_url = task["agent_output"]["pr_url"]

                # Format task info
                task_info = f"{task['run_id']} - {status}"
                if len(task_info) > width - 45:
                    task_info = task_info[:width - 48] + "..."
                stdscr.addstr(current_line, 2, task_info, status_color)

                # Draw description
                desc = task.get("description", "")
                if len(desc) > width - 45:
                    desc = desc[:width - 48] + "..."
                stdscr.addstr(current_line, 40, desc)
                current_line += 1

                # Draw PR URL on next line if available
                if pr_url and current_line < height - 2:
                    pr_indent = "    "  # 4 spaces indentation
                    pr_text = f"{pr_indent}PR: {pr_url}"
                    if len(pr_text) > width - 2:
                        pr_text = pr_text[:width - 5] + "..."
                    stdscr.addstr(current_line, 0, pr_text, curses.A_UNDERLINE)
                    current_line += 1

        # Draw footer
        footer = "Use arrow keys to navigate, Enter to select, 'd' to delete, 'q' to quit"
        stdscr.addstr(height - 1, 0, footer[:width - 1])

    def draw_task_screen(self, stdscr):
        """Draw the task details screen"""
        try:
            # Clear the entire screen first
            stdscr.clear()
            height, width = stdscr.getmaxyx()

            # Get selected task
            tasks = self.list_tasks()
            if not tasks or self.selected_task_idx >= len(tasks):
                self.current_screen = "main"
                return

            task = tasks[self.selected_task_idx]
            current_line = 0

            # Draw header with task ID
            stdscr.addstr(current_line, 0, f"Task: {task['run_id']}", curses.A_BOLD)
            current_line += 1
            stdscr.addstr(current_line, 0, "Press 'b' to go back, 'l' to view logs")
            current_line += 2

            # Draw status prominently with color
            status = task.get('agent_status', 'unknown')
            status_color = curses.A_NORMAL
            if status == "Completed":
                status_color = curses.A_BOLD | curses.color_pair(1)  # Green
            elif status == "Failed":
                status_color = curses.A_BOLD | curses.color_pair(2)  # Red
            elif status == "Running":
                status_color = curses.A_BOLD | curses.color_pair(3)  # Yellow

            stdscr.addstr(current_line, 0, "Status:", curses.A_BOLD)
            stdscr.addstr(current_line, 8, status, status_color)
            current_line += 1

            # Draw PR URL if available
            pr_url = None
            if task.get("agent_output", {}).get("pull_request_url"):
                pr_url = task["agent_output"]["pull_request_url"]
            elif task.get("agent_output", {}).get("pr_url"):
                pr_url = task["agent_output"]["pr_url"]

            if pr_url:
                stdscr.addstr(current_line, 0, "PR URL:", curses.A_BOLD)
                # Truncate URL if too long
                display_url = pr_url
                if len(display_url) > width - 10:
                    display_url = display_url[:width - 13] + "..."
                stdscr.addstr(current_line, 8, display_url, curses.A_UNDERLINE)
                current_line += 1

            # Draw pull request message if available
            pr_message = None
            if task.get("agent_output", {}).get("pull_request_message"):
                pr_message = task["agent_output"]["pull_request_message"]
            elif task.get("agent_output", {}).get("pr_message"):
                pr_message = task["agent_output"]["pr_message"]

            if pr_message:
                stdscr.addstr(current_line, 0, "Pull Request Message:", curses.A_BOLD)
                current_line += 1
                # Format and wrap the PR message
                message_lines = pr_message.split('\n')
                for line in message_lines:
                    # Handle long lines by wrapping them
                    while line:
                        if current_line >= height - 2:  # Leave space for footer
                            break
                        # Calculate how much of the line we can display
                        display_length = min(len(line), width - 2)
                        stdscr.addstr(current_line, 2, line[:display_length])
                        line = line[display_length:]
                        current_line += 1
                    if current_line >= height - 2:
                        break

            # Draw subtasks for Fullstack Planner tasks
            if task.get('agent_type') == "Fullstack Planner" and task.get('agent_output'):
                output = task['agent_output']
                if output.get('list_of_subtasks') and output.get('list_of_subtask_titles'):
                    current_line += 1
                    stdscr.addstr(current_line, 0, "Generated Subtasks:", curses.A_BOLD)
                    current_line += 1

                    # Calculate total content height
                    total_content_height = 0
                    for i, (title, subtask) in enumerate(zip(output['list_of_subtask_titles'], output['list_of_subtasks']), 1):
                        # Count lines for title
                        total_content_height += 1
                        # Count lines for description
                        subtask_lines = subtask.split('\n')
                        for line in subtask_lines:
                            # Count wrapped lines
                            total_content_height += (len(line) + width - 7) // (width - 6)
                        # Count lines for difficulty and assignee
                        total_content_height += 2
                        # Add space between subtasks
                        total_content_height += 1

                    # Calculate visible area
                    visible_height = height - current_line - 2  # Leave space for footer

                    # Calculate scroll position
                    max_scroll = max(0, total_content_height - visible_height)
                    self.task_scroll_pos = min(max(0, self.task_scroll_pos), max_scroll)

                    # Show subtasks with numbers and titles
                    current_content_line = 0
                    for i, (title, subtask) in enumerate(zip(output['list_of_subtask_titles'], output['list_of_subtasks']), 1):
                        # Skip if this subtask is above the visible area
                        if current_content_line < self.task_scroll_pos:
                            # Count lines for this subtask
                            subtask_lines = subtask.split('\n')
                            for line in subtask_lines:
                                current_content_line += (len(line) + width - 7) // (width - 6)
                            current_content_line += 3  # Title, difficulty, assignee
                            current_content_line += 1  # Space between subtasks
                            continue

                        # Stop if we've reached the bottom of the screen
                        if current_line >= height - 2:
                            break

                        # Show subtask number and title
                        subtask_header = f"{i}. {title}"
                        if i == self.selected_subtask_idx + 1:  # +1 because i starts at 1
                            stdscr.addstr(current_line, 2, subtask_header, curses.A_REVERSE)
                        else:
                            stdscr.addstr(current_line, 2, subtask_header, curses.A_BOLD)
                        current_line += 1
                        current_content_line += 1

                        # Show subtask description
                        subtask_lines = subtask.split('\n')
                        for line in subtask_lines:
                            if current_line >= height - 2:
                                break
                            # Wrap long lines
                            while line:
                                display_length = min(len(line), width - 6)
                                stdscr.addstr(current_line, 4, line[:display_length])
                                line = line[display_length:]
                                current_line += 1
                                current_content_line += 1
                                if current_line >= height - 2:
                                    break

                        # Show difficulty if available
                        if output.get('assessment_of_subtask_difficulty') and i <= len(output['assessment_of_subtask_difficulty']):
                            difficulty = output['assessment_of_subtask_difficulty'][i-1]
                            if current_line < height - 2:
                                stdscr.addstr(current_line, 4, f"Difficulty: {difficulty}")
                                current_line += 1
                                current_content_line += 1

                        # Show assignee info
                        if current_line < height - 2:
                            # Default to Agent for all subtasks since they can be handled by agents
                            assignee = "Agent"
                            stdscr.addstr(current_line, 4, f"Assignee: {assignee}")
                            current_line += 1
                            current_content_line += 1

                        current_line += 1  # Add space between subtasks
                        current_content_line += 1

                    # Add instructions for creating PM tasks
                    if current_line < height - 2:
                        current_line += 1
                        stdscr.addstr(current_line, 0, "Use arrow keys to select a subtask, 'p' to create PM task for selected subtask", curses.A_BOLD)
                        current_line += 1

            # Draw other task details in a more organized way
            details_start = current_line + 1
            if details_start < height - 2:
                stdscr.addstr(details_start, 0, "Task Details:", curses.A_BOLD)
                details_start += 1

                # Agent Type
                if details_start < height - 2:
                    stdscr.addstr(details_start, 2, f"Agent Type: {task.get('agent_type', 'unknown')}")
                    details_start += 1

                # Created/Updated times
                if details_start < height - 2:
                    stdscr.addstr(details_start, 2, f"Created: {task.get('created_at', 'unknown')}")
                    details_start += 1
                if details_start < height - 2:
                    stdscr.addstr(details_start, 2, f"Updated: {task.get('updated_at', 'unknown')}")
                    details_start += 1

                # Description
                if details_start < height - 2:
                    desc = task.get('description', '')
                    if desc:
                        stdscr.addstr(details_start, 2, "Description:", curses.A_BOLD)
                        details_start += 1
                        # Split description into lines and display each line
                        desc_lines = desc.split('\n')
                        for line in desc_lines:
                            if details_start >= height - 2:
                                break
                            # Wrap long lines
                            while line:
                                display_length = min(len(line), width - 4)
                                stdscr.addstr(details_start, 4, line[:display_length])
                                line = line[display_length:]
                                details_start += 1
                                if details_start >= height - 2:
                                    break

                # Repository info
                if details_start < height - 2:
                    if task.get("repos"):  # Fullstack Planner
                        stdscr.addstr(details_start, 2, "Repositories:")
                        details_start += 1
                        for repo in task["repos"]:
                            if details_start < height - 2:
                                stdscr.addstr(details_start, 4, repo)
                                details_start += 1
                    elif task.get("repo"):  # SWE/PM
                        stdscr.addstr(details_start, 2, f"Repository: {task['repo']}")
                        details_start += 1

                # Branch info
                if details_start < height - 2 and task.get("branch"):
                    stdscr.addstr(details_start, 2, f"Branch: {task['branch']}")
                    details_start += 1

            # Draw footer
            footer = "Press 'b' to go back, 'l' to view logs, ↑/↓ to scroll"
            try:
                stdscr.addstr(height - 1, 0, footer[:width - 1])
            except curses.error:
                pass  # Ignore if we can't write the footer

            # Refresh the screen
            stdscr.refresh()

        except Exception as e:
            logger.error(f"Error drawing task screen: {str(e)}")
            self.add_debug_message(f"Error displaying task: {str(e)}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            # Return to main screen on error
            self.current_screen = "main"

    def draw_log_screen(self, stdscr):
        """Draw the log screen for a selected task"""
        height, width = stdscr.getmaxyx()
        stdscr.clear()

        # Get the selected task
        task_id = self.selected_task
        task = self.task_storage.get_active_task(task_id) or {}

        # Draw header
        stdscr.addstr(0, 0, f"Logs for Task: {task_id}", curses.A_BOLD)
        stdscr.addstr(1, 0, "Press 'b' to go back, 'up/down' to scroll")

        # Prepare formatted output
        output_lines = []

        # Get logs from database
        try:
            # This is correct, we want logs for the task_id
            database_logs = self.task_storage.get_all_logs_for_task(task_id)
            if database_logs:
                output_lines.append("=== LOGS FROM DATABASE ===")
                for log_entry in database_logs:
                    output_lines.append(f"Run ID: {log_entry['run_id']}")
                    output_lines.append(f"Agent Type: {log_entry['agent_type']}")
                    output_lines.append(f"Created: {log_entry['created_at']}")
                    output_lines.append(f"Updated: {log_entry['updated_at']}")
                    output_lines.append("Log Data:")
                    try:
                        formatted_logs = json.dumps(log_entry['log_data'], indent=2, ensure_ascii=False)
                        output_lines.extend(formatted_logs.split('\n'))
                    except (TypeError, ValueError):
                        output_lines.append(str(log_entry['log_data']))
                    output_lines.append("")  # Empty line separator
                    output_lines.append("-" * 50)  # Visual separator
                    output_lines.append("")
            else:
                output_lines.append("=== LOGS FROM DATABASE ===")
                output_lines.append(f"No logs found in database for task: {task_id}")
                output_lines.append("")
        except Exception as e:
            output_lines.append("=== LOGS FROM DATABASE ===")
            output_lines.append(f"Error retrieving logs from database: {str(e)}")
            output_lines.append("")

        # Add agent output section if available
        agent_output = task.get("agent_output", {})
        if agent_output:
            output_lines.append("=== AGENT OUTPUT (from payload) ===")
            try:
                formatted_output = json.dumps(agent_output, indent=2, ensure_ascii=False)
                output_lines.extend(formatted_output.split('\n'))
            except (TypeError, ValueError):
                # Fallback if JSON serialization fails
                output_lines.append(str(agent_output))
            output_lines.append("")  # Empty line separator

        # Add raw logs dump section if available (legacy)
        raw_logs = task.get("raw_logs_dump", {})
        if raw_logs:
            output_lines.append("=== RAW LOGS DUMP (from payload) ===")
            try:
                formatted_logs = json.dumps(raw_logs, indent=2, ensure_ascii=False)
                output_lines.extend(formatted_logs.split('\n'))
            except (TypeError, ValueError):
                # Fallback if JSON serialization fails
                output_lines.append(str(raw_logs))
            output_lines.append("")

        # If no output or logs, show a message
        if not output_lines or all("not found" in line for line in output_lines if line):
            output_lines = ["No logs or output available for this task"]

        # Handle scrolling
        visible_lines = height - 3
        start_line = max(0, min(self.log_scroll_pos, len(output_lines) - visible_lines))
        end_line = min(len(output_lines), start_line + visible_lines)

        # Display the formatted lines
        for i, line_idx in enumerate(range(start_line, end_line)):
            line = output_lines[line_idx]
            # Truncate line if too long for screen
            if len(line) > width - 2:
                line = line[:width - 5] + "..."
            try:
                stdscr.addstr(i + 3, 0, line)
            except curses.error:
                # Handle cases where we can't write to screen
                pass

        # Show scroll indicator if there are more lines
        if len(output_lines) > visible_lines:
            scroll_info = f"[{start_line + 1}-{end_line} of {len(output_lines)} lines]"
            try:
                stdscr.addstr(height - 1, width - len(scroll_info) - 1, scroll_info, curses.A_DIM)
            except curses.error:
                pass

        stdscr.refresh()

    def handle_task_description_input(self, stdscr, key: int) -> bool:
        """Handle input for task description screen"""
        try:
            if key == curses.KEY_BACKSPACE or key == 127:
                if self.cursor_pos > 0:
                    # Handle backspace across multiple lines
                    lines = self.task_description.split('\n')
                    current_line = 0
                    remaining_pos = self.cursor_pos

                    # Find the current line
                    for i, line in enumerate(lines):
                        line_length = len(line)
                        if remaining_pos <= line_length:
                            current_line = i
                            break
                        remaining_pos -= line_length + 1

                    current_col = remaining_pos - 1

                    if current_col == 0 and current_line > 0:
                        # Backspace at start of line - merge with previous line
                        prev_line = lines[current_line - 1]
                        current_line_text = lines[current_line]
                        lines[current_line - 1] = prev_line + current_line_text
                        lines.pop(current_line)
                    else:
                        # Normal backspace within line
                        line = lines[current_line]
                        lines[current_line] = line[:current_col] + line[current_col + 1:]

                    self.task_description = '\n'.join(lines)
                    self.cursor_pos -= 1
            elif key == curses.KEY_LEFT:
                if self.cursor_pos > 0:
                    self.cursor_pos -= 1
            elif key == curses.KEY_RIGHT:
                if self.cursor_pos < len(self.task_description):
                    self.cursor_pos += 1
            elif key == curses.KEY_UP:
                # Move cursor up one line
                lines = self.task_description.split('\n')
                current_line = 0
                remaining_pos = self.cursor_pos

                # Find current line
                for i, line in enumerate(lines):
                    line_length = len(line)
                    if remaining_pos <= line_length:
                        current_line = i
                        break
                    remaining_pos -= line_length + 1

                current_col = remaining_pos - 1

                if current_line > 0:
                    # Move to previous line
                    prev_line_length = len(lines[current_line - 1])
                    self.cursor_pos -= (current_col + 1)  # Move to end of current line
                    self.cursor_pos -= 1  # Move past newline
                    self.cursor_pos -= prev_line_length  # Move to start of previous line
                    self.cursor_pos += min(current_col, prev_line_length)  # Move to same column
            elif key == curses.KEY_DOWN:
                # Move cursor down one line
                lines = self.task_description.split('\n')
                current_line = 0
                remaining_pos = self.cursor_pos

                # Find current line
                for i, line in enumerate(lines):
                    line_length = len(line)
                    if remaining_pos <= line_length:
                        current_line = i
                        break
                    remaining_pos -= line_length + 1

                current_col = remaining_pos - 1

                if current_line < len(lines) - 1:
                    # Move to next line
                    current_line_length = len(lines[current_line])
                    self.cursor_pos += (current_line_length - current_col)  # Move to end of current line
                    self.cursor_pos += 1  # Move past newline
                    self.cursor_pos += min(current_col, len(lines[current_line + 1]))  # Move to same column
            elif key == ord('\n') or key == 10:  # Enter key
                # Check for double Enter
                try:
                    # Set a small timeout to check for the next key
                    curses.halfdelay(1)  # 1/10th of a second timeout
                    next_key = stdscr.getch()
                    curses.cbreak()  # Reset to normal input mode

                    if next_key == ord('\n') or next_key == 10:  # Second Enter key
                        if self.task_description.strip():
                            return True
                except curses.error:
                    # If no next key or timeout, treat as normal Enter
                    curses.cbreak()  # Reset to normal input mode
                    pass

                # Normal Enter - insert newline
                lines = self.task_description.split('\n')
                current_line = 0
                remaining_pos = self.cursor_pos

                # Find current line
                for i, line in enumerate(lines):
                    line_length = len(line)
                    if remaining_pos <= line_length:
                        current_line = i
                        break
                    remaining_pos -= line_length + 1

                current_col = remaining_pos - 1

                # Split the current line at cursor position
                line = lines[current_line]
                lines[current_line] = line[:current_col + 1]
                lines.insert(current_line + 1, line[current_col + 1:])

                self.task_description = '\n'.join(lines)
                self.cursor_pos += 1
            elif key == 10 and curses.getch() == 10:  # Ctrl+Enter
                if self.task_description.strip():
                    return True
            elif key >= 32 and key <= 126:  # Printable ASCII characters
                lines = self.task_description.split('\n')
                current_line = 0
                remaining_pos = self.cursor_pos

                # Find current line
                for i, line in enumerate(lines):
                    line_length = len(line)
                    if remaining_pos <= line_length:
                        current_line = i
                        break
                    remaining_pos -= line_length + 1

                current_col = remaining_pos - 1

                # Insert character at cursor position
                line = lines[current_line]
                lines[current_line] = line[:current_col + 1] + chr(key) + line[current_col + 1:]

                self.task_description = '\n'.join(lines)
                self.cursor_pos += 1
            return False
        except Exception as e:
            logger.error(f"Error handling task description input: {str(e)}")
            self.add_debug_message(f"Error in task description: {str(e)}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return False

    def handle_repo_selection(self, key: int) -> bool:
        """Handle repository selection based on agent type"""
        if key == ord('q'):
            return True
        elif key == curses.KEY_UP:
            self.selected_repo_idx = max(0, self.selected_repo_idx - 1)
        elif key == curses.KEY_DOWN:
            self.selected_repo_idx = min(len(self.connected_repos) - 1, self.selected_repo_idx + 1)
        elif key == ord(' '):  # Space to toggle selection
            repo = self.connected_repos[self.selected_repo_idx]
            if repo in self.selected_repos:
                self.selected_repos.remove(repo)
            else:
                # For PM and SWE agents, only allow one repository
                if self.selected_agent in [1, 2]:  # PM or SWE
                    self.selected_repos = [repo]  # Replace any existing selection
                else:  # Fullstack Planner
                    self.selected_repos.append(repo)
        elif key == curses.KEY_ENTER or key == 10:
            # For PM and SWE agents, require exactly one repository
            if self.selected_agent in [1, 2] and len(self.selected_repos) != 1:
                return False
            # For Fullstack Planner, require at least one repository
            elif self.selected_agent == 0 and not self.selected_repos:
                return False
            return True
        return False

    def handle_input(self, stdscr, key):
        """Handle user input"""
        height, width = stdscr.getmaxyx()  # Get screen dimensions

        if key == ord('q') and self.current_screen != "task_description":
            return False

        if self.current_screen == "main":
            if key == ord('n'):
                self.current_screen = "new_task"
                self.selected_agent = 0
                self.selected_repos = []
                self.selected_repo_idx = 0
                self.add_debug_message("Started new task creation")
            elif key == ord('d'):  # Delete selected task
                tasks = self.list_tasks()
                if tasks and self.selected_task_idx < len(tasks):
                    task_id = tasks[self.selected_task_idx]["run_id"]
                    self.remove_task(task_id)
                    self.add_debug_message(f"Deleted task {task_id}")
            elif key == curses.KEY_UP:
                tasks = self.list_tasks()
                self.selected_task_idx = max(0, self.selected_task_idx - 1)
                if tasks and self.selected_task_idx < len(tasks):
                    self.selected_task_id = tasks[self.selected_task_idx]["run_id"]
            elif key == curses.KEY_DOWN:
                tasks = self.list_tasks()
                self.selected_task_idx = min(len(tasks) - 1, self.selected_task_idx + 1)
                if tasks and self.selected_task_idx < len(tasks):
                    self.selected_task_id = tasks[self.selected_task_idx]["run_id"]
            elif key == ord('\n') or key == curses.KEY_ENTER:
                tasks = self.list_tasks()
                if tasks and self.selected_task_idx < len(tasks):
                    try:
                        self.selected_task_id = tasks[self.selected_task_idx]["run_id"]
                        self.current_screen = "task"
                        self.selected_subtask_idx = 0  # Reset subtask selection
                        self.task_scroll_pos = 0  # Reset scroll position
                        self.add_debug_message(f"Viewing task {self.selected_task_id}")
                    except Exception as e:
                        logger.error(f"Error transitioning to task view: {str(e)}")
                        self.add_debug_message(f"Error viewing task: {str(e)}")
                        import traceback
                        logger.error(f"Traceback: {traceback.format_exc()}")
                        # Stay on main screen if there's an error
                        self.current_screen = "main"

        elif self.current_screen == "new_task":  # Agent selection screen
            if key == ord('q'):
                self.current_screen = "main"
            elif key == curses.KEY_UP:
                self.selected_agent = max(0, self.selected_agent - 1)
            elif key == curses.KEY_DOWN:
                self.selected_agent = min(2, self.selected_agent + 1)
            elif key == ord('\n'):
                self.current_screen = "repo_selection"
                agent_types = ["Fullstack Planner", "PM", "SWE"]
                self.add_debug_message(f"Selected agent type: {agent_types[self.selected_agent]}")

        elif self.current_screen == "repo_selection":
            if key == ord('b'):
                self.current_screen = "new_task"
            elif key == curses.KEY_UP:
                self.selected_repo_idx = max(0, self.selected_repo_idx - 1)
            elif key == curses.KEY_DOWN:
                self.selected_repo_idx = min(len(self.connected_repos) - 1, self.selected_repo_idx + 1)
            elif key == ord(' '):  # Space to toggle selection
                repo = self.connected_repos[self.selected_repo_idx]
                if repo in self.selected_repos:
                    self.selected_repos.remove(repo)
                else:
                    # For PM and SWE agents, only allow one repository
                    if self.selected_agent in [1, 2]:  # PM or SWE
                        self.selected_repos = [repo]  # Replace any existing selection
                    else:  # Fullstack Planner
                        self.selected_repos.append(repo)
                self.add_debug_message(f"Selected repos: {[f'{o}/{r}' for o, r in self.selected_repos]}")
            elif key == ord('\n'):
                # For PM and SWE agents, require exactly one repository
                if self.selected_agent in [1, 2] and len(self.selected_repos) != 1:
                    self.add_debug_message("PM/SWE agents require exactly one repository")
                    return True
                # For Fullstack Planner, require at least one repository
                elif self.selected_agent == 0 and not self.selected_repos:
                    self.add_debug_message("Fullstack Planner requires at least one repository")
                    return True
                self.current_screen = "task_description"
                self.task_description = ""
                self.cursor_pos = 0
                self.add_debug_message("Proceeding to task description")

        elif self.current_screen == "task":
            if key == ord('b'):
                self.current_screen = "main"
            elif key == ord('l'):
                self.current_screen = "log"
                self.log_scroll_pos = 0
            elif key == ord('p'):
                # Create PM task for selected subtask
                tasks = self.list_tasks()
                if tasks and self.selected_task_idx < len(tasks):
                    task = tasks[self.selected_task_idx]
                    if task.get('agent_type') == "Fullstack Planner" and task.get('agent_output'):
                        output = task['agent_output']
                        if output.get('list_of_subtasks') and output.get('list_of_subtask_titles'):
                            try:
                                # Get the selected subtask
                                if 0 <= self.selected_subtask_idx < len(output['list_of_subtask_titles']):
                                    title = output['list_of_subtask_titles'][self.selected_subtask_idx]
                                    subtask = output['list_of_subtasks'][self.selected_subtask_idx]
                                    repo = output.get('list_of_subtask_repos', [])[self.selected_subtask_idx] if output.get('list_of_subtask_repos') else None

                                    if not repo and task.get('repos'):
                                        repo = task['repos'][0]  # Use first repo if not specified

                                    if repo:
                                        # Create the PM task
                                        task_id = self.create_task_sync(
                                            "PM",
                                            f"{title}\n\n{subtask}",
                                            [repo]
                                        )
                                        self.add_debug_message(f"Created PM task {task_id} for subtask: {title}")
                                    else:
                                        self.add_debug_message("No repository specified for subtask")
                            except Exception as e:
                                logger.error(f"Error creating PM task: {str(e)}")
                                self.add_debug_message(f"Error creating PM task: {str(e)}")
                                import traceback
                                logger.error(f"Traceback: {traceback.format_exc()}")
            elif key == curses.KEY_UP:
                # Move up in subtask list
                try:
                    tasks = self.list_tasks()
                    if tasks and self.selected_task_idx < len(tasks):
                        task = tasks[self.selected_task_idx]
                        if task.get('agent_type') == "Fullstack Planner" and task.get('agent_output'):
                            output = task['agent_output']
                            if output.get('list_of_subtasks'):
                                # Move selection up
                                if self.selected_subtask_idx > 0:
                                    self.selected_subtask_idx -= 1
                                # Scroll up if needed
                                if self.task_scroll_pos > 0:
                                    self.task_scroll_pos = max(0, self.task_scroll_pos - 1)
                except Exception as e:
                    logger.error(f"Error handling up arrow: {str(e)}")
                    self.add_debug_message(f"Error scrolling up: {str(e)}")
            elif key == curses.KEY_DOWN:
                # Move down in subtask list
                try:
                    tasks = self.list_tasks()
                    if tasks and self.selected_task_idx < len(tasks):
                        task = tasks[self.selected_task_idx]
                        if task.get('agent_type') == "Fullstack Planner" and task.get('agent_output'):
                            output = task['agent_output']
                            if output.get('list_of_subtasks'):
                                # Calculate total content height
                                total_content_height = 0
                                for i, (title, subtask) in enumerate(zip(output['list_of_subtask_titles'], output['list_of_subtasks']), 1):
                                    # Count lines for title
                                    total_content_height += 1
                                    # Count lines for description
                                    subtask_lines = subtask.split('\n')
                                    for line in subtask_lines:
                                        # Count wrapped lines
                                        total_content_height += (len(line) + width - 7) // (width - 6)
                                    # Count lines for difficulty and assignee
                                    total_content_height += 2
                                    # Add space between subtasks
                                    total_content_height += 1

                                # Move selection down
                                if self.selected_subtask_idx < len(output['list_of_subtasks']) - 1:
                                    self.selected_subtask_idx += 1
                                    # Scroll down if needed
                                    self.task_scroll_pos = min(total_content_height - 1, self.task_scroll_pos + 1)
                except Exception as e:
                    logger.error(f"Error handling down arrow: {str(e)}")
                    self.add_debug_message(f"Error scrolling down: {str(e)}")
            elif key == ord('\n') or key == curses.KEY_ENTER:
                # Just ignore Enter key in task view
                pass

        elif self.current_screen == "log":
            if key == ord('b'):
                self.current_screen = "task"
            elif key == curses.KEY_UP:
                self.log_scroll_pos = max(0, self.log_scroll_pos - 1)
            elif key == curses.KEY_DOWN:
                self.log_scroll_pos += 1

        elif self.current_screen == "task_description":
            if self.handle_task_description_input(stdscr, key):
                # Create new task
                if self.task_description and self.selected_repos:
                    try:
                        # Map agent selection to agent type
                        agent_types = ["Fullstack Planner", "PM", "SWE"]
                        agent_type = agent_types[self.selected_agent]

                        # Convert selected repos to repo names
                        repos = [repo for owner, repo in self.selected_repos]

                        self.add_debug_message(f"Attempting to create task: agent_type={agent_type}, repos={repos}")

                        # Create task
                        task_id = self.create_task_sync(agent_type, self.task_description, repos)
                        logger.info(f"Created task {task_id} with agent type: {agent_type}")
                        self.add_debug_message(f"Successfully created task {task_id}")
                        self.current_screen = "main"
                    except Exception as e:
                        logger.error(f"Error creating task: {str(e)}")
                        self.add_debug_message(f"Failed to create task: {str(e)}")
                        import traceback
                        logger.error(f"Traceback: {traceback.format_exc()}")

        return True

    def run(self, stdscr):
        """Run the manager UI"""
        # Initialize colors
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_GREEN, -1)  # Success
        curses.init_pair(2, curses.COLOR_RED, -1)    # Error
        curses.init_pair(3, curses.COLOR_YELLOW, -1) # Warning

        # Hide cursor
        curses.curs_set(0)

        # Enable keypad mode
        stdscr.keypad(True)

        # Set initial screen
        self.current_screen = "main"
        self.add_debug_message("Started UI, waiting for event loop...")

        # Set up non-blocking input
        stdscr.nodelay(True)

        # Main loop
        running = True
        last_refresh = time.time()
        refresh_interval = 1.0  # Refresh every second

        while running:
            try:
                # Clear screen
                stdscr.clear()

                # Draw current screen
                if self.current_screen == "main":
                    self.draw_main_screen(stdscr)
                elif self.current_screen == "task":
                    self.draw_task_screen(stdscr)
                elif self.current_screen == "log":
                    self.draw_log_screen(stdscr)
                elif self.current_screen == "new_task":
                    self.draw_agent_selection_screen(stdscr)
                elif self.current_screen == "repo_selection":
                    self.draw_repo_selection_screen(stdscr)
                elif self.current_screen == "task_description":
                    self.draw_task_description_screen(stdscr)

                # Refresh screen
                stdscr.refresh()

                # Check for input (non-blocking)
                try:
                    key = stdscr.getch()
                    if key != -1:  # -1 means no input available
                        running = self.handle_input(stdscr, key)
                except curses.error:
                    pass  # No input available

                # Auto-refresh task status
                current_time = time.time()
                if current_time - last_refresh >= refresh_interval:
                    # Update task statuses
                    for task_id, future in list(self.running_tasks.items()):
                        if future.done():
                            try:
                                result = future.result()
                                if task_id in self.active_tasks:
                                    # Update the task with the result
                                    self.active_tasks[task_id].update(result)

                                    # Check for PR URL in the result
                                    pr_url = None
                                    if result.get("agent_output", {}).get("pull_request_url"):
                                        pr_url = result["agent_output"]["pull_request_url"]
                                    elif result.get("agent_output", {}).get("pr_url"):
                                        pr_url = result["agent_output"]["pr_url"]

                                    if pr_url:
                                        # Update the task's agent_output with the PR URL
                                        if "agent_output" not in self.active_tasks[task_id]:
                                            self.active_tasks[task_id]["agent_output"] = {}
                                        self.active_tasks[task_id]["agent_output"]["pull_request_url"] = pr_url
                                        self.add_debug_message(f"Updated PR URL for task {task_id}")
                            except Exception as e:
                                logger.error(f"Error getting task result: {str(e)}")
                            finally:
                                del self.running_tasks[task_id]
                    last_refresh = current_time

                # Small sleep to prevent high CPU usage
                time.sleep(0.1)

            except Exception as e:
                logger.error(f"Error in UI loop: {str(e)}")
                self.add_debug_message(f"UI Error: {str(e)}")
                import traceback
                logger.error(f"Traceback: {traceback.format_exc()}")
                running = False

    def monitor_worker_processes(self):
        """Monitor worker processes and update task status"""
        while True:
            try:
                completed_tasks = []
                for task_id, process in self.running_tasks.items():
                    # Check if process has completed
                    if process.poll() is not None:
                        # Process has finished
                        return_code = process.returncode
                        completed_tasks.append(task_id)

                        # Log subprocess completion
                        subprocess_logger.info(f"Subprocess completed: PID={process.pid}, task_id={task_id}, return_code={return_code}")

                        # Update task status in database if needed
                        task = self.get_task(task_id)
                        if task and task.get("agent_status") == "Running":
                            if return_code == 0:
                                # Let the worker process update status, but check if it didn't
                                # Refresh task from database
                                updated_task = self.task_storage.get_active_task_persistent(task_id)
                                if updated_task and updated_task.get("agent_status") == "Running":
                                    updated_task["agent_status"] = "Completed"
                                    updated_task["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                                    subprocess_logger.info(f"Subprocess task marked as completed: task_id={task_id}")
                            else:
                                task["agent_status"] = "Failed"
                                task["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                                subprocess_logger.info(f"Subprocess task marked as failed: task_id={task_id}, return_code={return_code}")

                        logger.info(f"Worker process for task {task_id} completed with return code {return_code}")

                # Remove completed tasks from running_tasks
                for task_id in completed_tasks:
                    del self.running_tasks[task_id]

                time.sleep(1)  # Check every second

            except Exception as e:
                logger.error(f"Error in process monitor: {str(e)}")
                time.sleep(5)  # Wait longer on error

    def remove_task(self, task_id: str) -> None:
        """Remove a task from active tasks and terminate if running"""
        try:
            # Terminate the running worker process if it exists
            if task_id in self.running_tasks:
                process = self.running_tasks[task_id]
                if process.poll() is None:
                    logger.info(f"Terminating worker process for task {task_id}")

                    # Try to terminate the process group first
                    try:
                        if hasattr(os, 'killpg'):
                            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                        else:
                            process.terminate()
                        subprocess_logger.info(f"Subprocess termination requested in remove_task: PID={process.pid}, task_id={task_id}, signal=SIGTERM")
                    except (OSError, ProcessLookupError):
                        process.terminate()
                        subprocess_logger.info(f"Subprocess termination fallback in remove_task: PID={process.pid}, task_id={task_id}, signal=SIGTERM")

                    try:
                        process.wait(timeout=5)
                        subprocess_logger.info(f"Subprocess terminated cleanly in remove_task: PID={process.pid}, task_id={task_id}")
                    except subprocess.TimeoutExpired:
                        subprocess_logger.warning(f"Subprocess force kill required in remove_task: PID={process.pid}, task_id={task_id}")
                        try:
                            if hasattr(os, 'killpg'):
                                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                            else:
                                process.kill()
                            process.wait(timeout=2)
                            subprocess_logger.info(f"Subprocess force killed in remove_task: PID={process.pid}, task_id={task_id}, signal=SIGKILL")
                        except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
                            subprocess_logger.warning(f"Subprocess kill failed or process already dead in remove_task: PID={process.pid}, task_id={task_id}")
                            pass

                # Close pipes to prevent resource leaks
                try:
                    if process.stdout and not process.stdout.closed:
                        process.stdout.close()
                    if process.stderr and not process.stderr.closed:
                        process.stderr.close()
                    if process.stdin and not process.stdin.closed:
                        process.stdin.close()
                except Exception as e:
                    logger.warning(f"Error closing pipes for task {task_id}: {e}")

                # Wait for worker threads
                if hasattr(self, 'worker_threads') and task_id in self.worker_threads:
                    threads = self.worker_threads[task_id]
                    try:
                        threads['stdout_thread'].join(timeout=3)
                        threads['stderr_thread'].join(timeout=3)
                    except Exception as e:
                        logger.warning(f"Error joining worker threads for task {task_id}: {e}")
                    del self.worker_threads[task_id]

                del self.running_tasks[task_id]
                logger.info(f"Terminated worker process for task {task_id}")

            # Remove from active tasks
            if task_id in self.active_tasks:
                del self.active_tasks[task_id]
                logger.info(f"Removed task {task_id} from active tasks")

            # Remove from database
            self.task_storage.remove_active_task(task_id)

        except Exception as e:
            logger.error(f"Error removing task {task_id}: {str(e)}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")

def run_manager():
    """Run the manager with process monitoring"""
    # Set terminal type if not already set
    if not os.environ.get('TERM'):
        os.environ['TERM'] = 'xterm-256color'

    # Clear screen before starting
    os.system('clear')

    # Create the manager
    manager = WorkerManager()

    # Flag to track if cleanup is in progress
    cleanup_in_progress = False

    # Set up signal handlers for clean shutdown
    def signal_handler(signum, frame):
        nonlocal cleanup_in_progress
        if cleanup_in_progress:
            print("\nForce exit...")
            os._exit(1)  # Force exit if cleanup is taking too long

        cleanup_in_progress = True
        print(f"\nReceived signal {signum}, shutting down gracefully...")
        try:
            manager.cleanup()
            print("Cleanup completed, exiting.")
        except Exception as e:
            print(f"Error during cleanup: {e}")
        finally:
            sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        # Start process monitoring thread
        monitor_thread = threading.Thread(target=manager.monitor_worker_processes, daemon=True)
        monitor_thread.start()
        manager.add_debug_message("Process monitor thread started")

        # Run the curses UI in the main thread
        curses_wrapper(manager.run)

    except KeyboardInterrupt:
        print("\nInterrupted by user")
    except Exception as e:
        logger.error(f"Unexpected error in main: {e}")
    finally:
        # Clean up when the program exits
        if not cleanup_in_progress:
            manager.cleanup()

if __name__ == "__main__":
    run_manager()
