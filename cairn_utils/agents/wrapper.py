"""
This provides some utility code that wraps the three agent classes (swe, pm, explorer).

The wrappers are used easily run the agents in a format that allows them to output their live statuses to a curses panel. This is handled
by mutating python dictionaries that are stored in memory, allowing for things like status updates, etc.


These dicts also serve as the payload.

Example payload formats:

Fullstack Planner:
{
    "run_id": "1234567890",
    "created_at": "2025-06-04 12:00:00",
    "updated_at": "2025-06-04 12:00:00",
    "repos": ["repo1", "repo2", "repo3"],
    "owner": "owner1",
    "description": "description of the fullstack task",
    "subtask_ids": [],
    "agent_output": {}, # relevant output dict from the agent as specified in tool_types.py
    "agent_status": "one of 'Queued', 'Running', 'Subtasks Generated', 'Subtasks Running', 'Completed', 'Failed'",
    "agent_type": "Fullstack Planner",
    "raw_logs_dump": {"optional": "lol"}
}

PM:
{
    "run_id": "1234567890",
    "created_at": "2025-06-04 12:00:00",
    "updated_at": "2025-06-04 12:00:00",
    "repo": "repo1",
    "owner": "owner1",
    "description": "description of the subtask",
    "agent_output": {},
    "agent_status": "one of 'Queued', 'Running', 'Completed', 'Failed'",
    "agent_type": "PM",
    "related_run_ids": ["1234567890", "1234567890", "1234567890"],
    "raw_logs_dump": {"optional": "lol"},
    "branch": "branch1"
}



SWE:
{
    "run_id": "1234567890", # a unique id
    "created_at": "2025-06-04 12:00:00", # the time the run was started.
    "updated_at": "2025-06-04 12:00:00", # the time the run was last updated.
    "repo": "repo1", # the repo the agent is working on.
    "owner": "owner1", # the owner of the repo.
    "description": "description of the subtask", # the description of the subtask.
    "agent_output": {}, # the output of the agent. as specified in tool_types.py
    "agent_status": "one of 'Queued', 'Running', 'Completed', 'Failed'",
    "agent_type": "SWE",
    "related_run_ids": ["1234567890", "1234567890", "1234567890"], # used for message passing / spying
    "raw_logs_dump": {}, # a dump of the raw logs from the agent.
    "branch": "branch1"
}

"""


import os
import time
import traceback
import logging
import json
from dotenv import load_dotenv

from .fullstack_planner import ExplorerAgent
from .pm import ProjectManagerAgent
from .swe import SoftwareEngineerAgent
from ..task_storage import TaskStorage

# Configure logging
logger = logging.getLogger(__name__)

async def wrapper(payload: dict) -> dict:
    """
    Unified wrapper for all agent types: SWE, PM, and Fullstack Planner.
    """

    agent_type = payload.get("agent_type")
    if not agent_type:
        payload.update({
            "agent_status": "Failed",
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
        })
        print("ERROR: No agent_type specified in payload")
        return payload

    # Load environment variables
    load_dotenv()

    # Load repository configurations
    try:
        with open("repos.json", "r") as f:
            repo_configs = json.load(f)["connected_repos"]
    except (FileNotFoundError, json.JSONDecodeError) as e:
        payload.update({
            "agent_status": "Failed",
            "error": f"Error loading or parsing repos.json: {e}",
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
        })
        print(f"ERROR: Could not load or parse repos.json: {e}")
        return payload

    # Determine owner and repo(s) from payload
    owner = payload.get("owner")
    repo_name = payload.get("repo") # For SWE/PM
    repo_names = payload.get("repos", []) # For Fullstack Planner

    if not owner:
        payload.update({
            "agent_status": "Failed",
            "error": "Owner not specified in payload",
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
        })
        print("ERROR: Owner not specified in payload")
        return payload

    target_repo = repo_name if repo_name else (repo_names[0] if repo_names else None)

    if not target_repo:
        payload.update({
            "agent_status": "Failed",
            "error": "No repository specified in payload",
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
        })
        print("ERROR: No repository specified in payload")
        return payload

    # Find the installation ID for the given owner and repo
    installation_id = None
    for repo_config in repo_configs:
        if repo_config["owner"] == owner and repo_config["name"] == target_repo:
            installation_id = repo_config["installation_id"]
            break

    if not installation_id:
        error_msg = f"No installation ID found for {owner}/{target_repo} in repos.json"
        payload.update({
            "agent_status": "Failed",
            "error": error_msg,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
        })
        print(f"ERROR: {error_msg}")
        return payload

    # Create a unique run ID if not provided
    if not payload.get("run_id"):
        timestamp = int(time.time())
        if agent_type == "SWE":
            run_id = f"swe_run_{timestamp}"
        elif agent_type == "PM":
            run_id = f"pm_run_{timestamp}"
        elif agent_type == "Fullstack Planner":
            run_id = f"fullstack_run_{timestamp}"
        else:
            payload.update({
                "agent_status": "Failed",
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
            })
            print(f"ERROR: Unsupported agent_type: {agent_type}")
            return payload
        payload["run_id"] = run_id
    else:
        run_id = payload.get("run_id")

    has_sibling_subtasks = 'sibling_subtask_ids' in payload and payload['sibling_subtask_ids']


    other_agents = []

    if has_sibling_subtasks:
        # get the subtask_ids from the payload
        parent_fullstack_id = payload.get("parent_fullstack_id")

        # Initialize task storage to get information about sibling subtasks
        task_storage = TaskStorage()

        # First try to get the parent fullstack task info
        if parent_fullstack_id:
            parent_payload = task_storage.get_active_task(parent_fullstack_id)

            if parent_payload:
                # If parent task has output with subtask information, extract it
                agent_output = parent_payload.get("agent_output", {})
                if agent_output and "list_of_subtasks" in agent_output:
                    subtask_index = payload.get("subtask_index")
                    if subtask_index is not None:

                        # Extract subtask info from parent's output
                        subtasks = agent_output.get("list_of_subtasks", [])
                        subtask_titles = agent_output.get("list_of_subtask_titles", [])
                        subtask_repos = agent_output.get("list_of_subtask_repos", [])

                        # get all except for the current index subtask
                        for i in range(len(subtasks)):
                            if i != subtask_index:
                                subtask_id = payload.get("sibling_subtask_ids")[i]
                                other_agents.append({
                                    "run_id": subtask_id,
                                    "repo": subtask_repos[i],
                                    "description": subtasks[i]
                                })

    # Handle branch creation for SWE and PM agents
    branch = None
    if agent_type in ["SWE", "PM"]:
        if not payload.get("branch"):
            timestamp = int(time.time())
            branch = f"{agent_type.lower()}-{timestamp}"
            payload["branch"] = branch
        else:
            branch = payload.get("branch")

    payload.update({
        "agent_status": "Running",
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
    })

    model_name = os.getenv("ANTHROPIC_MODEL_NAME")

    # Create and setup the appropriate agent
    try:
        if agent_type == "SWE":
            agent = SoftwareEngineerAgent()
            await agent.setup(
                owner=payload.get("owner"),
                repos=[payload.get("repo")],
                installation_id=installation_id,
                branch=branch,
                live_logging=False,
                run_id=run_id,
                subtask_id=None,
                model_name=model_name,
                running_locally=True,
                other_agents=other_agents if other_agents else None
            )
        elif agent_type == "PM":
            agent = ProjectManagerAgent()
            await agent.setup(
                owner=payload.get("owner"),
                repos=[payload.get("repo")],
                installation_id=installation_id,
                branch=branch,
                live_logging=False,
                run_id=run_id,
                subtask_id=None,
                model_name=model_name,
                running_locally=True,
                other_agents=other_agents if other_agents else None
            )
        elif agent_type == "Fullstack Planner":
            agent = ExplorerAgent()
            await agent.setup(
                owner=payload.get("owner"),
                repos=payload.get("repos", []),
                installation_id=installation_id,
                branch=None,
                live_logging=False,
                run_id=run_id,
                subtask_id=None,
                model_name=model_name,
                running_locally=True,
            )

        # Run the agent
        final_state = await agent.implement_task(payload.get("description")) if agent_type == "SWE" else await agent.run(payload.get("description"))
        generated_output = final_state['tool_outputs'][-1]['tool_output']

        payload.update({
            "agent_output": generated_output,
            "agent_status": "Completed",
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
        })

        # If this is a completed Fullstack Planner task, pre-generate subtask IDs
        if agent_type == "Fullstack Planner" and payload.get("agent_status") == "Completed":
            try:
                # Extract subtask information
                subtasks = generated_output.get("list_of_subtasks", [])
                if subtasks:
                    # Initialize TaskStorage
                    task_storage = TaskStorage()

                    # Pre-generate and store subtask IDs
                    generated_ids = task_storage.pre_generate_subtask_ids(
                        fullstack_run_id=run_id,
                        num_subtasks=len(subtasks)
                    )

                    # Add the generated IDs to the payload
                    payload["subtask_ids"] = [item["subtask_id"] for item in generated_ids]

                    logger.info(f"Pre-generated {len(generated_ids)} subtask IDs for Fullstack Planner task {run_id}")
            except Exception as e:
                logger.error(f"Error pre-generating subtask IDs: {e}")
                # Continue even if there's an error - we don't want to fail the task

        return payload

    except Exception as e:
        print(f"ERROR: Exception in wrapper: {e}")
        print(traceback.format_exc())

        payload.update({
            "agent_status": "Failed",
            "error": str(e),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
        })
        return payload

# Legacy function for backward compatibility
async def swe_wrapper(payload: dict) -> dict:
    """
    Legacy wrapper for SWE agent. Use wrapper() instead.
    """
    payload["agent_type"] = "SWE"
    return await wrapper(payload)

if __name__ == "__main__":
    import asyncio

    # Example payloads for testing different agent types

    # SWE agent example
    swe_payload = {
        "run_id": f"swe_run_{int(time.time())}",
        "repo": "test",
        "owner": "cairn-dev",
        "description": "call the generate output tool, I am testing something",
        "agent_output": {},
        "agent_status": "Queued",
        "agent_type": "SWE",
        "related_run_ids": [],
        "raw_logs_dump": {},
    }

    # PM agent example
    pm_payload = {
        "run_id": f"pm_run_{int(time.time())}",
        "repo": "test",
        "owner": "cairn-dev",
        "description": "please call delegate task tool with a description saying implement an endpoint that returns a random emoji. if you dont see the delegate task tool tell me.",
        "agent_output": {},
        "agent_status": "Queued",
        "agent_type": "PM",
        "related_run_ids": [],
        "raw_logs_dump": {},
    }

    # Fullstack Planner agent example
    fullstack_payload = {
        "run_id": f"fullstack_run_{int(time.time())}",
        "repos": ["test", "frontend"],
        "owner": "cairn-dev",
        "description": "Analyze the fullstack architecture and create subtasks",
        "subtask_ids": [],
        "agent_output": {},
        "agent_status": "Queued",
        "agent_type": "Fullstack Planner",
        "raw_logs_dump": {}
    }

    # Run one of the examples
    print("Running SWE agent wrapper...")
    result = asyncio.run(wrapper(pm_payload))
    print('#'*100)
    print(result)
