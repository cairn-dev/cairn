"""
A langgraph version of the SoftwareEngineer class, similar to how langgraph_explorer.py is a langgraph version of the ExplorerAgent class.
"""

import asyncio
import json
import time
import sys
import os
from typing import Dict, List, Any, Optional, Union
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage
from langchain_core.prompts import PromptTemplate
from langgraph.graph import StateGraph, END
from datetime import datetime
from dotenv import load_dotenv

# Fix imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent_classes import CodeEditorToolBox
from task_storage import TaskStorage

# Add current directory to sys.path for agent_consts
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from llm_consts import ChatAnthropic, ChatOpenAI
from agent_consts import AgentState, STRUCTURED_SWE_PROMPT
from thought_logger import AgentLogger
# from supabase_utils import get_supabase_client, get_other_agents_from_subtask_id
from langgraph_utils import (
    create_agent_graph,
    create_run_config,
    print_run_start,
    print_run_end,
    format_other_agents_info,
)


class SoftwareEngineerAgent:
    """A LangGraph agent that uses CodeEditorToolBox tools to implement code changes."""

    def __init__(self):
        """Initialize the SoftwareEngineerAgent with default values."""
        self.graph = None
        self.toolbox = None
        self.logger = None
        self.llm_client = None
        self.live_logging = False
        self.run_id = None
        self.branch = None
        self.subtask_id = None
        self.other_agents = None
        self.running_locally = False

    async def setup(
        self,
        owner: str,
        repos: list[str],
        installation_id: int,
        branch: str,
        model_name: str = "claude-3-7-sonnet-latest",
        llm_client=None,
        live_logging=False,
        supabase_client=None,
        run_id=None,
        subtask_id=None,
        running_locally=False,
        running_from_pm=False,
        other_agents=None,
        fake_calls_path: str = None
    ):
        """
        Set up the agent with necessary components.

        Args:
            owner (str): GitHub repository owner
            repos (list[str]): List of repositories
            installation_id (int): GitHub installation ID
            branch (str): Repository branch
            model_name (str): LLM model name
            llm_client: LLM client to use. If None, creates new one.
            live_logging (bool): Whether to print live logs
            supabase_client: Supabase client for logging
            run_id (str): Run ID for logging
            subtask_id (str): Subtask ID for coordination
            running_locally (bool): Whether running locally
            running_from_pm (bool): Whether running from project manager
            other_agents (list[dict]): list of other agents' info to use for coordination.
            -> dict should have keys of run_id, description, and repo (each keyed to a string)
            fake_calls_path (str): Path to JSON file containing fake LLM responses for testing

        Returns:
            SoftwareEngineerAgent: The configured agent instance
        """
        if not branch:
            raise ValueError("Branch is required for SoftwareEngineerAgent")

        # Set instance variables
        self.live_logging = live_logging
        self.run_id = run_id or str(int(time.time()))
        self.branch = branch
        self.subtask_id = subtask_id
        self.running_locally = running_locally
        self.other_agents = other_agents

        # Setup clients and dependencies
        await self._setup_clients()
        await self._setup_toolbox(owner, repos, installation_id, branch, model_name)
        self._setup_llm_and_logger(llm_client, model_name, fake_calls_path)
        await self._setup_graph(repos, branch)

        if self.live_logging:
            print(f"Software Engineer agent ready on branch '{self.branch}'.")

        return self

    async def _setup_clients(self):
        """A method to setup clients for the agent. This is a placeholder for now."""

    async def _setup_toolbox(self, owner, repos, installation_id, branch, model_name):
        """Initialize and authenticate the toolbox."""
        self.toolbox = CodeEditorToolBox(
            owner,
            repos,
            installation_id,
            branch,
            model_name,
            self.subtask_id,
            self.other_agents,
            running_locally=self.running_locally,
        )
        await self.toolbox.authenticate()

        if self.live_logging:
            print(f"Authentication complete. Branch '{branch}' ready.")

    def _setup_llm_and_logger(self, llm_client, model_name, fake_calls_path=None):
        """Setup LLM client and logger."""
        if not self.logger:
            self.logger = AgentLogger(
                run_id=self.run_id,
            )

        self.llm_client = ChatOpenAI(model=model_name, raw_logging=True) #  llm_client or ChatAnthropic(model=model_name)

        # Load fake responses if path is provided
        if fake_calls_path and os.path.exists(fake_calls_path):
            try:
                with open(fake_calls_path, "r") as f:
                    fake_calls = json.load(f)
                for fake_call in fake_calls.get("fake_calls", []):
                    self.llm_client.add_fake_response(fake_call)
                if self.live_logging:
                    print(f"Loaded fake responses from {fake_calls_path}")
            except Exception as e:
                raise RuntimeError(f"Failed to load fake responses from {fake_calls_path}. This is a fatal error as test responses are required: {str(e)}")

    async def _setup_graph(self, repos, branch):
        """Create and configure the agent graph."""
        tools = self.toolbox.get_all_tools()
        tools_dict = {tool["name"]: tool for tool in tools}
        tool_names = list(tools_dict.keys())

        other_agents_info = format_other_agents_info(self.other_agents)

        prompt = STRUCTURED_SWE_PROMPT.partial(
            tools="\n".join([f"{tool['name']}: {tool['description']}" for tool in tools]),
            tool_names=", ".join(tool_names),
            available_repos=", ".join(repos),
            branch=branch,
            other_agents_info=other_agents_info,
        )

        self.graph = create_agent_graph(
            tools=tools,
            prompt=prompt,
            llm_client=self.llm_client,
            logger=self.logger,
            toolbox=self.toolbox,
            state_type=AgentState,
        )

    async def implement_task(self, task_description: str, run_id: str = None) -> Dict[str, Any]:
        """
        Run the agent with the given task description.

        Args:
            task_description (str): The task to implement
            run_id (str): Unique identifier for the run

        Returns:
            Dict: The final state after completion

        Raises:
            ValueError: If the agent has not been set up
        """
        if self.graph is None:
            raise ValueError("Agent not set up. Call setup() first.")

        # Update run_id if provided
        if run_id:
            self.run_id = run_id
            # Reinitialize the logger with the new run_id to ensure proper logging
            self.logger = AgentLogger(run_id=run_id)
            # Also update the logger in the graph to ensure all logs go to the correct place
            self.graph.logger = self.logger

        initial_state = AgentState(user_input=task_description)

        # print_run_start(
        #     f"SOFTWARE ENGINEER AGENT RUN WITH TASK: {task_description}",
        #     self.live_logging,
        # )

        config = create_run_config(self.run_id)
        result = await self.graph.ainvoke(initial_state, config=config)

        # print_run_end(self.live_logging)

        return result


async def debug_logs(run_id: str):
    """Retrieve and print logs from the SQLite database for debugging."""
    task_storage = TaskStorage()
    logs = task_storage.load_log(run_id, "agent_logger")

    print("\n==== AGENT LOGS FROM DATABASE ====")
    print(f"Run ID: {run_id}")
    print("Last updated:", logs.get("last_updated", "N/A"))
    print(f"Total messages: {len(logs.get('progress', []))}")

    # Print log entries with timestamps
    for i, message in enumerate(logs.get("progress", [])):
        print(f"\n--- Message {i+1} ---")
        if isinstance(message, dict):
            for key, value in message.items():
                if key == "content" and isinstance(value, str) and len(value) > 500:
                    # Truncate long content
                    print(f"{key}: {value[:500]}... [truncated]")
                else:
                    print(f"{key}: {value}")
        else:
            print(message)

    print("\n==== END OF LOGS ====")


# Example usage
async def main(owner: str = "cairn-dev", repos: List[str] = ["test"]):
    """Example usage of SoftwareEngineerAgent"""
    from dotenv import load_dotenv

    # Load environment variables
    load_dotenv()

    # Create a unique branch for this run
    timestamp = int(time.time())
    branch = f"swe-agent-test-{timestamp}"

    # Create a unique run ID
    run_id = branch

    # Demonstration of a task
    task_description = "please call the view repositoiry structure tool, then gneerate an output describing what you saw."

    # Create and setup the agent
    agent = SoftwareEngineerAgent()
    await agent.setup(
        owner=owner,
        repos=repos,
        installation_id=65848345,
        branch=branch,
        live_logging=True,
        run_id=run_id,
        # subtask_id="970f06f8-03de-4e76-9386-6feed924358c",
        model_name="gpt-4o",
        running_locally=True,
        # other_agents = [{
        #     "run_id": "test_run_SEE_OTHER_AGENTS2",
        #     "description": "add a function to the backend that fetches a string of random emojis",
        #     "repo": "cairn-dev/backend"
        # }]
        # fake_calls_path="testing/fake_anthropic_calls.json"
    )

    # Run the agent and write final state to JSON
    final_state = await agent.implement_task(task_description)

    # Display logs from the database for debugging
    await debug_logs(run_id)

    # return final_state


if __name__ == "__main__":
    asyncio.run(main())
