"""
Tools for AGENTS.

Agent classes inherit tools from the DefaultToolBox class.
"""

import asyncio

from github_utils import (
    create_branch_from_default,
    create_pull_request,
    get_default_branch_sha,
    list_files_in_repo,
)
from tool_types import ExplorerResponse, PMResponse, PMResponseWithPR, SWEResponse
from toolbox import DefaultToolBox


def structure_fcn_for_anthropic(tools):
    """
    Format tools for Anthropic's tool calling API.

    Args:
        tools: A list of tuples where each tuple contains (function, function_name, function_schema)

    Returns:
        A list of dictionaries formatted for Anthropic's tool calling API.
    """

    tools_structured = []

    for tool in tools:
        # The tool tuple is (function, function_name, function_schema)
        function_obj, name, function_schema, description = tool

        # Build the Anthropic‐formatted tool dict
        tool_dict = {
            "name": name,
            "description": description,
            "input_schema": {
                "type": "object",
                "properties": function_schema.get("properties", {}),
                "required": function_schema.get("required", []),
            },
            # Keep the actual function object around for internal dispatch
            "function": function_obj,
        }

        tools_structured.append(tool_dict)

    return tools_structured


class CodeEditorToolBox(DefaultToolBox):
    def __init__(
        self,
        owner: str,
        repos: list[str],
        installation_id: int,
        branch: str,
        model_name: str = "claude-3-7-sonnet-latest",
        subtask_id=None,
        other_agents=None,
        running_locally=False,
    ):
        super().__init__(
            owner,
            repos,
            installation_id,
            branch,
            model_name,
            subtask_id=subtask_id,
            other_agents=other_agents,
            running_locally=running_locally,
        )
        self.branch_created = False

    async def authenticate(self):
        """
        Authenticate with GitHub and ensure the specified branch exists.
        If the branch doesn't exist, creates it based on the default branch.
        """
        # First get the installation token
        await super().authenticate()

        # Check if a branch is specified and ensure it exists
        if self.branch and not self.branch_created:
            try:
                # Try to list files in the branch to see if it exists
                await list_files_in_repo(
                    self.installation_token,
                    self.owner,
                    self.repo,
                    "",
                    branch=self.branch,
                )
                print(f"Branch '{self.branch}' already exists.")
            except Exception as e:
                if "404" in str(e):  # Branch doesn't exist
                    # print(f"Branch '{self.branch}' doesn't exist. Creating it now...")
                    await create_branch_from_default(
                        self.installation_token, self.owner, self.repo, self.branch
                    )
                    # print(f"Branch '{self.branch}' created successfully.")
                    self.branch_created = True
                else:
                    # If it's another error, just log it and continue
                    print(f"Warning: Error checking branch: {str(e)}")
        if not self.branch:
            raise ValueError("No branch specified for code editor tool box")

    def get_all_tools(self):
        """
        Returns a list of tools specifically for the Software Engineer agent.
        """

        # Multi tool call should be added last, as it depends on all the other tools
        tool_name_2_function = {
            "edit_file_descriptively": self.get_edit_file_descriptively_tool(),
            "edit_file": self.get_edit_file_tool(),
            "view_repository_structure": self.get_view_repository_structure_tool(),
            "list_files": self.get_list_files_tool(),
            "read_file": self.get_read_file_tool(),
            "generate_output": self.get_generate_output_tool(),
            "spy_on_agent": self.get_spy_on_agent_tool(),
            "search_files_by_name": self.get_search_files_by_name_tool(),
            "substring_search": self.get_substring_search_tool(),
        }

        tools = list(tool_name_2_function.values())

        tools.append(self.get_batch_tool_call_tool(tool_name_2_function))

        return structure_fcn_for_anthropic(tools)

    def get_generate_output_tool(self):
        async def generate_output(input: dict) -> dict:

            output = SWEResponse(**input).model_dump()
            output["end_task"] = True
            return output

        description = """
            Generate a structured output for the Software Engineer agent.

            This tool is used to format the final response of the SWE agent according to the
            expected schema with implementation details, files modified, and verification status.

            Args:
                input_data: A SWEResponse object containing:
                    - summary_of_changes: Summary of the changes made
                    - files_modified: List of files that were modified
                    - verification_status: Whether the changes were successfully verified
                    - error_messages: List of error messages encountered, if any
                    - additional_notes: Any additional notes about the implementation

            Returns:
                SWEResponse: A structured response conforming to the SWEResponse schema.
            """

        function_name = "generate_output"
        function_schema = SWEResponse.model_json_schema()

        return generate_output, function_name, function_schema, description


class ManagerToolBox(DefaultToolBox):
    def __init__(
        self,
        owner: str,
        repos: list[str],
        installation_id: int,
        branch: str,
        model_name: str = "claude-3-7-sonnet-latest",
        subtask_id=None,
        other_agents=None,
        running_locally=False,
        parent_run_id=None,
    ):
        super().__init__(
            owner,
            repos,
            installation_id,
            branch,
            model_name,
            subtask_id=subtask_id,
            other_agents=other_agents,
            running_locally=running_locally,
        )
        self.branch_created = False
        self.parent_run_id = parent_run_id  # Store the parent task's run_id for linking

    async def authenticate(self):
        """
        Authenticate with GitHub and ensure the specified branch exists.
        If the branch doesn't exist, creates it based on the default branch.
        """
        # First get the installation token
        await super().authenticate()

        # Check if a branch is specified and ensure it exists
        if self.branch and not self.branch_created:
            try:
                # Try to list files in the branch to see if it exists
                await list_files_in_repo(
                    self.installation_token,
                    self.owner,
                    self.repo,
                    "",
                    branch=self.branch,
                )
                print(f"Branch '{self.branch}' already exists.")
            except Exception as e:
                if "404" in str(e):  # Branch doesn't exist
                    print(f"Branch '{self.branch}' doesn't exist. Creating it now...")
                    await create_branch_from_default(
                        self.installation_token, self.owner, self.repo, self.branch
                    )
                    print(f"Branch '{self.branch}' created successfully.")
                    self.branch_created = True
                else:
                    # If it's another error, just log it and continue
                    print(f"Warning: Error checking branch: {str(e)}")

    def get_all_tools(self):
        """
        Returns a list of tools specifically for the Project Manager agent.
        """

        # Multi tool call should be added last, as it depends on all the other tools
        tool_name_2_function = {
            "view_repository_structure": self.get_view_repository_structure_tool(),
            "list_files": self.get_list_files_tool(),
            "read_file": self.get_read_file_tool(),
            "generate_output": self.get_generate_output_tool(),
            "delegate_task": self.get_delegate_task_tool(),
            "spy_on_agent": self.get_spy_on_agent_tool(),
            "search_files_by_name": self.get_search_files_by_name_tool(),
            "substring_search": self.get_substring_search_tool(),
        }

        tools = list(tool_name_2_function.values())
        tools.append(self.get_batch_tool_call_tool(tool_name_2_function))

        return structure_fcn_for_anthropic(tools)

    async def create_pull_request_for_branch(self, pr_title: str, pr_body: str) -> dict:
        """
        Create a pull request for the current branch against the default branch.

        Args:
            pr_title: Title for the pull request
            pr_body: Body/description for the pull request

        Returns:
            dict: The API response from GitHub containing the PR details
        """
        if not self.branch:
            raise ValueError("No branch specified for pull request creation")

        if not self.installation_token:
            await self.authenticate()

        # Get the default branch name to use as the base
        default_branch, _ = await get_default_branch_sha(
            self.installation_token, self.owner, self.repo
        )

        # Create the pull request
        pr_response = await create_pull_request(
            self.installation_token,
            self.owner,
            self.repo,
            head=self.branch,  # The branch with changes
            base=default_branch,  # The target branch (usually main/master)
            title=pr_title,
            body=pr_body,
        )

        return pr_response

    def set_swe(self, swe_agent):
        """SETTER METHOD: set the swe_agent for the PM agent."""
        self.swe = swe_agent

    # Override the generate_output tool for PM to create PRs automatically
    def get_generate_output_tool(self):
        async def generate_output(input: dict) -> dict:

            try:

                output = PMResponse(**input)

                response_dict = output.model_dump()
                # Create pull request if we have a branch
                if self.branch:
                    try:
                        # Extract a title from the PR message (first line or first 50 chars)
                        pr_message = response_dict["pull_request_message"]
                        pr_title_lines = pr_message.split("\n")
                        pr_title = (
                            pr_title_lines[0] if pr_title_lines else "Automated PR"
                        )

                        # Limit title length
                        if len(pr_title) > 100:
                            pr_title = pr_title[:97] + "..."

                        # Create the PR
                        pr_response = await self.create_pull_request_for_branch(
                            pr_title=pr_title, pr_body=pr_message
                        )

                        # Add PR URL to the response
                        response_dict["pr_url"] = pr_response.get(
                            "html_url", "PR created but URL not available"
                        )
                    except Exception as e:
                        # Add PR error to the response but don't fail
                        if response_dict.get("issues_encountered"):
                            response_dict["issues_encountered"].append(
                                f"Failed to create PR: {str(e)}"
                            )
                        else:
                            response_dict["issues_encountered"] = [
                                f"Failed to create PR: {str(e)}"
                            ]

                response_dict["end_task"] = True
                return response_dict
            except Exception as e:
                # If there's any error, return a structured error response
                error_msg = f"Error generating output: {str(e)}"
                return {
                    "recommendations": ["Error occurred while generating recommendations"],
                    "issues_encountered": [error_msg],
                    "pull_request_message": "Error occurred while generating the pull request message. Please check the issues encountered.",
                    "end_task": False,
                }

        description = """
            Generate a structured output for the PM agent and create a pull request.

            This tool is used to format the final response of the PM agent according to the
            expected schema with recommendations, issues encountered, and a pull request message.
            When this is called, it will also automatically create a pull request using the
            pull_request_message from the response.

            Expected structure:
            {
                "recommendations": ["Recommendation 1", "Recommendation 2", ...],
                "issues_encountered": ["Issue 1", "Issue 2", ...],
                "pull_request_message": "A detailed PR message with markdown formatting",
            }

            Returns:
                PMResponse: A structured response with all necessary components and PR information
            """

        function_name = "generate_output"
        function_schema = PMResponseWithPR.model_json_schema()

        return generate_output, function_name, function_schema, description


class ExplorerToolBox(DefaultToolBox):
    def __init__(
        self,
        owner: str,
        repos: list[str],
        installation_id: int,
        branch: str = None,
        subtask_id=None,
        other_agents=None,
        running_locally=False,
    ):
        super().__init__(
            owner,
            repos,
            installation_id,
            branch,
            subtask_id=subtask_id,
            other_agents=other_agents,
            running_locally=running_locally,
        )

    def get_all_tools(self):
        """
        Returns a list of tools specifically for the Explorer agent.
        """

        # Multi tool call should be added last, as it depends on all the other tools
        tool_name_2_function = {
            "view_repository_structure": self.get_view_repository_structure_tool(),
            "list_files": self.get_list_files_tool(),
            "read_file": self.get_read_file_tool(),
            "generate_output": self.get_generate_output_tool(),
            "search_files_by_name": self.get_search_files_by_name_tool(),
            "substring_search": self.get_substring_search_tool(),
        }

        if not self.has_1_repo:
            tool_name_2_function["switch_repo"] = self.get_switch_repo_tool()

        tools = list(tool_name_2_function.values())

        tools.append(self.get_batch_tool_call_tool(tool_name_2_function))

        return structure_fcn_for_anthropic(tools)

    def get_generate_output_tool(self):
        async def generate_output(input: dict) -> ExplorerResponse:

            output = ExplorerResponse(**input).model_dump()
            output["end_task"] = True

            return output

        description = """
            This function generates a structured output for the Explorer Agent that summarizes
            the problem, subtasks to accomplish it, and provides detailed analysis.

            Expected Schema:
            {
                "summary_of_the_problem": str,  # Detailed analysis of the problem/task
                "response_to_the_question": str | None,  # Direct answer if input was a question
                "most_relevant_code_file_paths": list[str],  # List of relevant file paths
                "list_of_subtasks": list[str] | None,  # List of subtasks, each describing a specific task to accomplish
                "list_of_subtask_titles": list[str] | None,  # High-level titles for each subtask
                "list_of_subtask_repos": list[str] | None,  # Repository for each subtask
                "assessment_of_difficulty": "high" | "medium" | "low" | "unknown",
                "assessment_of_subtask_difficulty": list[list[str]] | None,  # Difficulty per subtask
                "assessment_of_subtask_assignment": list[str] | None,  # "agent" or "human" per subtask
                "recommended_approach": str  # Detailed explanation of approach
            }

            Example:
            {
                "summary_of_the_problem": "Need to implement user authentication system with OAuth2",
                "response_to_the_question": null,
                "most_relevant_code_file_paths": ["auth/oauth.py", "models/user.py"],
                "list_of_subtasks": [
                    "Set up OAuth2 provider and implement authentication flow",
                    "Create user model and add database migrations"
                ],
                "list_of_subtask_titles": ["OAuth Implementation", "User Model Setup"],
                "list_of_subtask_repos": ["backend", "backend"],
                "assessment_of_difficulty": "medium",
                "assessment_of_subtask_difficulty": [["medium"], ["low"]],
                "assessment_of_subtask_assignment": ["agent", "agent"],
                "recommended_approach": "Start with implementing OAuth2 provider integration..."
            }

            If your input is not valid you will receive an error message explaining why, and you should retry.
            """

        function_name = "generate_output"
        function_schema = ExplorerResponse.model_json_schema()

        return generate_output, function_name, function_schema, description


# if __name__ == "__main__":

#     owner = "cairn-dev"
#     repo = "test"
#     installation_id = 65037960
#     jwt_token = generate_jwt()
#     installtion_token = asyncio.run(get_installation_token(jwt_token, installation_id))
#     toolbox = ExplorerToolBox(
#         owner=owner, repos=[repo], installation_id=installation_id
#     )
#     print(toolbox.get_all_tools()[0]["description"])
