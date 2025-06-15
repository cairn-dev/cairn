"""
Defines wrappers around Anthropic API to add tool calling and other features.

Currently only supports Anthropic models.

NOTE: This module is being deprecated in favor of the LangChain-based implementation
in cairn_utils/agents/langchain_llm.py. See MIGRATION_GUIDE.md for instructions
on how to migrate.
"""

import aiohttp
import os
import datetime
import pathlib
from typing import List, Optional, Dict, Any, Union
import json
from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    """
    Pydantic model for a single tool call.

    Represents the structure returned by get_tool_calls() method.
    """
    id: str = Field(..., description="Unique identifier for the tool call")
    name: str = Field(..., description="Name of the tool to call")
    input: Dict[str, Any] = Field(..., description="Input parameters for the tool. Fed directly to the tool in toolbox.py or agent_classes.py")
    type: Optional[str] = Field(None, description="Type of the tool call. Use 'tool_use' for client-side tool calls and 'server_tool_use' for server-side tool calls.")
    server_executed: Optional[bool] = Field(None, description="Whether the tool was executed server-side")


class ToolResult(BaseModel):
    """
    Pydantic model for a single tool result.

    Represents the structure of individual values in the dict returned by get_tool_results() method.
    """
    content: Union[str, List[Any]] = Field(..., description="The result content")
    type: str = Field(..., description="Type identifier for the result")
    id: Optional[str] = Field(None, description="ID of the tool call this result corresponds to")


class LLMResponseData(BaseModel):
    """
    Pydantic model for the complete LLM response data.

    Combines the outputs of get_tool_calls(), get_tool_results(), and get_text_content() methods.
    """
    tool_calls: List[ToolCall] = Field(default_factory=list, description="List of tool calls from the response")
    tool_results: Dict[str, ToolResult] = Field(default_factory=dict, description="Dictionary mapping tool call IDs to their results")
    text_content: str = Field(default="", description="The raw text content from the response (i.e. the thought, message, or response), excluding tool calls")


class LLMResponse:
    """
    Base class for LLM response handling with standardized interface for LangGraph.

    Just defines standard getter methods for the ToolCalls, ToolResults, and TextContent.
    """

    def __init__(self, content: Union[str, List[Dict[str, Any]]], status_code: int = 200, raw_logging: bool = False):
        """
        Initialize an LLM response. The getter methods are used by LangGraphUtils to parse the response.

        Args:
            content: Raw content from the LLM response
            status_code: HTTP status code of the response
            raw_logging: Whether to log parsed response to a file
        """
        self.content = content
        self.text_content = ""
        self.status_code = status_code
        self.tool_calls: List[ToolCall] = []
        self.tool_results: Dict[str, ToolResult] = {} # maps the id of the tool call to the result.
        self.raw_logging = raw_logging

        # Process content to extract tool calls and results
        self._process_content()

        # Log parsed response if enabled
        if self.raw_logging:
            self._log_parsed_response()

    def _process_content(self):
        """
        Base implementation of content processing. Should be overridden by subclasses.
        """
        raise NotImplementedError("Subclasses must implement this method")

    def _log_parsed_response(self):
        """
        Logs the parsed response to a JSON file if raw_logging is enabled.
        """
        if not self.raw_logging:
            return

        # Create logs directory if it doesn't exist
        log_dir = pathlib.Path("logs/llm_logs")
        log_dir.mkdir(parents=True, exist_ok=True)

        # Generate timestamp for the filename
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        log_file = log_dir / f"{timestamp}_parsed_response.json"

        # Get response data as a dictionary
        response_data = self.get_response_data().dict()

        # Write response data to file
        with open(log_file, "w") as f:
            json.dump(response_data, f, indent=2, default=str)

    def get_tool_calls(self) -> List[ToolCall]:
        """Return list of ToolCall Pydantic models."""
        return self.tool_calls

    def get_tool_results(self) -> Dict[str, ToolResult]:
        """Return dictionary mapping tool call IDs to ToolResult Pydantic models."""
        return self.tool_results

    def get_text_content(self) -> str:
        """Return the text content as a string."""
        return self.text_content

    def get_response_data(self) -> LLMResponseData:
        """Return the complete response data as an LLMResponseData Pydantic model."""
        return LLMResponseData(
            tool_calls=self.get_tool_calls(),
            tool_results=self.get_tool_results(),
            text_content=self.get_text_content()
        )



class AnthropicResponse(LLMResponse):
    """
    Anthropic-specific response wrapper that inherits from LLMResponse.

    Handles the definition of _process_content() in order to extract tool calls, tool results, and text content.
    """

    def __init__(self, content: Union[str, List[Dict[str, Any]]], status_code: int = 200, raw_logging: bool = False):
        """
        Initialize an Anthropic response wrapper.

        Args:
            content: Raw content from the Anthropic API response
            status_code: HTTP status code of the response
            raw_logging: Whether to log the parsed response to a file
        """
        super().__init__(content, status_code, raw_logging)

    def _process_content(self):
        """
        Process content blocks to extract tool calls, results, and text content for Anthropic format.

        Anthropic returns content as a list of blocks with different types:
        - "text": Contains the model's text response
        - "server_tool_use": Contains a tool call executed server-side
        - "web_search_tool_result": Contains results from a web search
        """

        # Handle string-only content directly
        if isinstance(self.content, str):
            self.text_content = self.content
            return

        # Process list of content blocks
        if isinstance(self.content, list):
            text_parts = []
            for block in self.content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        # Extract text content
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        # Create ToolCall Pydantic model
                        tool_call = ToolCall(
                            id=block.get("id", ""),
                            name=block.get("name", ""),
                            input=block.get("input", {}),
                            type="tool_use",
                            server_executed=False
                        )
                        self.tool_calls.append(tool_call)
                    elif block.get("type") == "server_tool_use":
                        # Create ToolCall Pydantic model
                        tool_call = ToolCall(
                            id=block.get("id", ""),
                            name=block.get("name", ""),
                            input=block.get("input", {}),
                            type="server_tool_use",
                            server_executed=True
                        )
                        self.tool_calls.append(tool_call)
                    elif block.get("type") == "web_search_tool_result":
                        # Create ToolResult Pydantic model
                        type =block.get("type", "")
                        id = block.get("tool_use_id", "")
                        content_processed = []
                        if type == "web_search_tool_result":
                            content = block.get("content", {})
                            for content_block in content:
                                content_processed.append({
                                    "type": content_block.get("type", ""),
                                    "title": content_block.get("title", ""),
                                    "url": content_block.get("url", ""),
                                })

                        tool_result = ToolResult(
                            content=content_processed,
                            type=type,
                            id=id
                        )
                        self.tool_results[block.get("id", "")] = tool_result

                    # Not supporting other content blcoks (i.e. citations, etc.)
                    else:
                        continue

            # Set the concatenated text content
            self.text_content = "".join(text_parts)

        print(f"\n[DEBUG] Text content: {self.text_content}")
        print(f"\n[DEBUG] Tool calls: {self.tool_calls}")
        print(f"\n[DEBUG] Tool results: {self.tool_results}")





class ChatLLM:
    """
    Generic base class with some utility methods for LLM API calls.

    NOTE: see example_aiinvoke_input.json for an example of the input format to ainvoke.

    The format follows Anthropic's Messages API convention with:
    1. System message with plain text content
    2. User messages with either plain text or tool_result content blocks
    3. Assistant messages with text content and optional tool_use blocks

    Testing Support:
    ---------------
    This class includes support for "fake responses" to facilitate testing without making actual API calls.
    This is useful for:
    - Unit testing without API costs
    - Simulating specific response scenarios
    - Recording and replaying real API responses
    - Development without hitting API rate limits

    When using fake responses, the class will raise an error if they run out rather than
    falling back to real API calls. This helps catch cases where more API calls are being
    made than expected.

    Example usage:
    ```python
    # Add a fake response
    ChatLLM.add_fake_response({
        "content": [
            {"type": "text", "text": "This is a fake response"},
            {"type": "tool_use", "id": "123", "name": "some_tool", "input": {}}
        ]
    })

    # The next API call will return this fake response
    response = await llm_client.ainvoke(messages)

    # A second API call will raise an error since we're out of fake responses
    await llm_client.ainvoke(messages)  # raises NoRemainingFakeResponsesError

    # Clear fake responses when done
    ChatLLM.clear_fake_responses()
    ```
    """

    # Class variable to store fake responses
    _fake_responses = []
    # Flag to indicate if fake responses were ever added
    _using_fake_responses = False

    def __init__(self, model: str, api_key: Optional[str] = None, raw_logging: bool = False):
        """Initialize a chat with an LLM with a model str and an API key."""
        self.model = model
        if not self.model or not isinstance(self.model, str):
            raise ValueError("Invalid model name. Must be a non-empty string.")
        self.api_key = api_key
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable or api_key parameter must be set")
        self.raw_logging = raw_logging  # Use the passed parameter instead of hardcoding
        print(f"\n[DEBUG] ChatLLM initialized with raw_logging={self.raw_logging}")

    @classmethod
    def add_fake_response(cls, response_data: Dict[str, Any], status_code: int = 200):
        """
        Add a fake response to be returned instead of making an API call.
        Responses will be returned in FIFO order (first added = first returned).
        Each response is used exactly once and then removed from the queue.

        Args:
            response_data: The response data to return. Should match the format of real API responses:
                         For Anthropic, this is typically a dict with a "content" key containing
                         a list of content blocks (text, tool_use, etc.)
            status_code: The HTTP status code to return with the response. Defaults to 200.
                       Can be used to simulate error conditions (e.g., 429 for rate limits).

        Example:
            ```python
            ChatLLM.add_fake_response({
                "content": [{"type": "text", "text": "Test response"}]
            })
            ```
        """
        cls._fake_responses.append((response_data, status_code))
        cls._using_fake_responses = True
        print(f"\n[DEBUG] Added fake response (total: {len(cls._fake_responses)})")
        if isinstance(response_data.get("content"), list):
            # For structured responses, show the types of content blocks
            content_types = [block.get("type") for block in response_data["content"] if isinstance(block, dict)]
            print(f"[DEBUG] Response contains content blocks: {content_types}")
        elif isinstance(response_data.get("content"), str):
            # For simple text responses, show a preview
            preview = response_data["content"][:100] + "..." if len(response_data["content"]) > 100 else response_data["content"]
            print(f"[DEBUG] Response contains text: {preview}")

    @classmethod
    def clear_fake_responses(cls):
        """
        Clear all fake responses from the queue.

        Example:
            ```python
            def tearDown(self):
                ChatLLM.clear_fake_responses()
            ```
        """
        count = len(cls._fake_responses)
        cls._fake_responses = []
        cls._using_fake_responses = False
        print(f"\n[DEBUG] Cleared {count} fake response(s)")

    def _filter_messages(self, messages: List[Dict[str, Any]]) -> str:
        """
        Extract the system prompt from the rest of the messages.

        Returns: system_prompt, rest_of_messages
        """
        system_prompt = None
        rest_of_messages = []
        for message in messages:
            if message.get("role") == "system":
                system_prompt = message.get("content", "")
            else:
                rest_of_messages.append(message)
        return system_prompt, rest_of_messages

    def _log_api_payload(self, payload: Dict[str, Any]):
        """
        Logs the API payload to a JSON file if raw_logging is enabled.

        Args:
            payload: The API request payload to log
        """
        if not self.raw_logging:
            return

        # Create logs directory if it doesn't exist
        log_dir = pathlib.Path("logs/llm_logs")
        log_dir.mkdir(parents=True, exist_ok=True)

        # Generate timestamp for the filename
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        log_file = log_dir / f"{timestamp}_api_payload.json"

        # Write payload to file
        with open(log_file, "w") as f:
            json.dump(payload, f, indent=2, default=str)

    async def _make_api_request(self, url: str, payload: Dict[str, Any], headers: Dict[str, str]):
        """
        Generic method to make API requests with proper error handling.
        Supports fake responses for testing purposes.

        If fake responses have been added via add_fake_response(), this method will:
        1. Return and remove the oldest fake response instead of making an API call
        2. Return the specified status code with the fake response
        3. Raise NoRemainingFakeResponsesError if fake responses run out

        Args:
            url: API endpoint URL
            payload: Request payload
            headers: Request headers

        Returns:
            Response data and status code as a tuple (response_data, status_code)

        Raises:
            NoRemainingFakeResponsesError: If fake responses were used but have run out
            Exception: For API errors (status != 200) or network issues
        """

        # Log the API payload if raw_logging is enabled
        if self.raw_logging:
            self._log_api_payload(payload)


        # Check for fake responses first
        if self._fake_responses:
            response_data, status_code = self._fake_responses.pop(0)
            remaining = len(self._fake_responses)
            print(f"\n[DEBUG] Using fake response (remaining: {remaining})")
            if isinstance(response_data.get("content"), list):
                content_types = [block.get("type") for block in response_data["content"] if isinstance(block, dict)]
                print(f"[DEBUG] Response contains content blocks: {content_types}")
            return response_data, status_code
        elif self._using_fake_responses:
            # If we were using fake responses but ran out, raise an error
            print("\n[DEBUG] ERROR: No remaining fake responses but _using_fake_responses=True")
            raise NoRemainingFakeResponsesError(
                "No remaining fake responses. This error is raised instead of making real "
                "API calls when fake responses were previously added but have run out. "
                "This usually indicates that more API calls are being made than expected."
            )



        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=300),
                ) as response:
                    status_code = response.status
                    if response.status != 200:
                        error_text = await response.text()
                        error = Exception(f"API call failed with status {response.status}: {error_text}")
                        error.status_code = status_code  # Attach status code to exception
                        raise error

                    response_data = await response.json()
                    return response_data, status_code
        except aiohttp.ClientError as e:
            # Handle network-related errors
            status_code = getattr(e, 'status', 0)
            if not status_code and hasattr(e, 'message'):
                # Try to extract status code from error message
                import re
                status_match = re.search(r'(\d{3})', str(e.message))
                if status_match:
                    status_code = int(status_match.group(1))

            error = Exception(f"Network error: {str(e)}")
            error.status_code = status_code
            raise error


class NoRemainingFakeResponsesError(Exception):
    """Raised when fake responses were used but have run out."""
    pass


class ChatAnthropic(ChatLLM):
    """Direct Anthropic API client with tool calling support."""

    def __init__(self, model: str, api_key: Optional[str] = None, raw_logging: bool = False):
        """
        Initialize an Anthropic API client.

        Args:
            model: The Anthropic model to use
            api_key: Optional API key. If not provided, will try to get it from environment
            raw_logging: Whether to log API calls and responses to files
        """
        # Get API key from environment if not provided
        if not api_key:
            api_key = os.environ.get("ANTHROPIC_API_KEY")

        # Initialize the base class with the model and API key
        super().__init__(model, api_key, raw_logging)

    async def ainvoke(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        server_tools: Optional[Dict[str, Dict[str, Any]]] = None,
        tool_choice: Optional[Union[str, Dict[str, Any]]] = None,
        **kwargs,
    ) -> AnthropicResponse:
        """
        Invoke the model with messages and optional tool calling support.

        Args:
            messages: The messages to send to the model (can include system messages)
            tools: List of client-side tools in Anthropic format
            server_tools: Dictionary of server-side tools handled by Anthropic
            tool_choice: Tool choice configuration ("auto", "any", or specific tool)
            **kwargs: Additional kwargs to pass to the model

        Returns:
            AnthropicResponse with the model's response and status code
        """

        system_prompt, filtered_messages = self._filter_messages(messages)

        # Build API payload
        payload = {
            "model": self.model,
            "messages": filtered_messages,
            "system": system_prompt,
            "max_tokens": kwargs.get("max_tokens", 4096),
            **{k: v for k, v in kwargs.items() if k in ["temperature", "top_p", "top_k"]}
        }

        # Combine client-side and server-side tools
        api_tools = []

        if tools:
            api_tools.extend(tools)

        if server_tools:
            # Add server-side tools to the API payload
            for tool_config in server_tools.values():
                # Extract only the fields needed for the API
                api_tool = {
                    "type": tool_config["type"],
                    "name": tool_config["name"]
                }
                if "max_uses" in tool_config:
                    api_tool["max_uses"] = tool_config["max_uses"]
                api_tools.append(api_tool)

        if api_tools:
            payload["tools"] = api_tools
        if tool_choice:
            payload["tool_choice"] = tool_choice

        # Make API call
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }

        # Make the API request and wrap the response
        response_data, status_code = await self._make_api_request(
            "https://api.anthropic.com/v1/messages",
            payload,
            headers
        )

        # # save response content to the list within the json file called fake_calls.json, which has a single key "fake_calls"
        # with open("testing/fake_anthropic_calls.json", "r") as f:
        #     fake_calls = json.load(f)
        # fake_calls["fake_calls"].append(response_data)
        # with open("testing/fake_anthropic_calls.json", "w") as f:
        #     json.dump(fake_calls, f, indent=2, default=str)


        return AnthropicResponse(response_data.get("content", []), status_code=status_code, raw_logging=self.raw_logging)



class OpenAIResponse(LLMResponse):
    """
    OpenAI-specific response wrapper that inherits from LLMResponse.

    Handles parsing of OpenAI API responses to extract tool calls, tool results, and text content.
    """

    def __init__(self, content: Union[Dict[str, Any], List[Dict[str, Any]]], status_code: int = 200, raw_logging: bool = False):
        """
        Initialize an OpenAI response wrapper.

        Args:
            content: Raw content from the OpenAI API response (typically a dict with 'choices')
            status_code: HTTP status code of the response
            raw_logging: Whether to log the parsed response to a file
        """

        super().__init__(content, status_code, raw_logging)

    def _process_content(self):
        """
        Process OpenAI response to extract tool calls and text content.

        OpenAI returns responses with a 'choices' array, where each choice has a 'message' with:
        - 'content': The text response (can be null if tool calls are present)
        - 'tool_calls': List of tool call objects (optional)
        - 'finish_reason': Indicates if response stopped due to tool calls
        """

        # get the first choice
        if isinstance(self.content, dict) and 'choices' in self.content:
            message_content = self.content['choices'][0].get('message', {})
            self.finish_reason = self.content['choices'][0].get('finish_reason', '')
        else:
            message_content = self.content
            self.finish_reason = ''


        # parse messages content...
        text_content = message_content['content']
        self.text_content = text_content if text_content else ""

        # parse tool calls...
        for tool_call in message_content.get("tool_calls", []):
            id = tool_call.get("id")
            function = tool_call.get("function")
            name = function.get("name")
            args = function.get("arguments")

            # load the args (OpenAI uses a json string for the args)
            args = json.loads(args)
            self.tool_calls.append(
                ToolCall(
                    id=id,
                    name=name,
                    input=args,
                    type="tool_use",
                    server_executed=False
                )
            )




class ChatOpenAI(ChatLLM):
    """OpenAI API client with tool calling support."""

    def __init__(self, model: str, api_key: Optional[str] = None, raw_logging: bool = False):
        """
        Initialize an OpenAI API client.

        Args:
            model: The OpenAI model to use (e.g., 'gpt-4o')
            api_key: Optional API key. If not provided, will try to get it from environment
            raw_logging: Whether to log API calls and responses to files
        """
        # Get API key from environment if not provided
        if not api_key:
            api_key = os.environ.get("OPENAI_API_KEY")

        super().__init__(model, api_key, raw_logging)

    async def ainvoke(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        server_tools: Optional[Dict[str, Dict[str, Any]]] = None,
        tool_choice: Optional[Union[str, Dict[str, Any]]] = None,
        **kwargs,
    ) -> OpenAIResponse:
        """
        Invoke the OpenAI model with messages and optional tool calling support.

        Args:
            messages: The messages to send to the model (can include system messages)
            tools: List of client-side tools in OpenAI format
            server_tools: Dictionary of server-side tools (not supported by OpenAI, included for compatibility)
            tool_choice: Tool choice configuration ("auto", "none", or {"type": "function", "function": {"name": "tool_name"}})
            **kwargs: Additional kwargs to pass to the model (e.g., temperature, max_tokens)

        Returns:
            OpenAIResponse with the model's response and status code
        """
        system_prompt, filtered_messages = self._filter_messages(messages)

        # Convert messages to OpenAI format
        openai_messages = []

        # Add system message if present
        if system_prompt:
            openai_messages.append({"role": "system", "content": system_prompt})

        # Process other messages
        for message in filtered_messages:
            role = message["role"]
            content = message["content"]

            # Handle string content directly
            if isinstance(content, str):
                openai_messages.append({"role": role, "content": content})
                continue

            # Handle list content (tool results, etc)
            if isinstance(content, list):
                # For assistant messages with tool calls
                if role == "assistant":
                    msg = {"role": role, "content": "", "tool_calls": []}
                    for block in content:
                        if block["type"] == "text":
                            msg["content"] = block["text"]
                        elif block["type"] == "tool_use":
                            msg["tool_calls"].append({
                                "id": block["id"],
                                "type": "function",
                                "function": {
                                    "name": block["name"],
                                    "arguments": json.dumps(block["input"])
                                }
                            })
                    openai_messages.append(msg)
                # For user messages with tool results
                elif role == "user":
                    tool_results = []
                    for block in content:
                        if block["type"] == "tool_result":
                            tool_results.append({
                                "tool_call_id": block["tool_use_id"],
                                "role": "tool",
                                "name": block.get("name", ""),  # OpenAI needs the tool name
                                "content": str(block["content"])  # Convert content to string
                            })
                    # Add tool results as separate messages
                    openai_messages.extend(tool_results)

        # Build API payload
        payload = {
            "model": self.model,
            "messages": openai_messages,
            "max_tokens": kwargs.get("max_tokens", 4096),
            **{k: v for k, v in kwargs.items() if k in ["temperature", "top_p"]}
        }

        # Format tools for OpenAI
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.get("name", ""),
                        "description": tool.get("description", ""),
                        "parameters": tool.get("input_schema", {}),
                        "strict": False # enforce strict schema.
                    }
                } for tool in tools
            ]

            # set additionalParameters to be false
            for tool in payload["tools"]:
                tool["function"]["parameters"]["additionalProperties"] = False

            payload["tool_choice"] = "auto" # let openai select which (if any) tools to use...

        # Make API call
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

        # Log the API payload if raw_logging is enabled
        if self.raw_logging:
            self._log_api_payload(payload)

        # Make the API request and wrap the response
        response_data, status_code = await self._make_api_request(
            "https://api.openai.com/v1/chat/completions",
            payload,
            headers
        )

        # save response content to the list within the json file called fake_calls.json, which has a single key "fake_calls"
        with open("testing/fake_openai_calls.json", "r") as f:
            fake_calls = json.load(f)
        fake_calls["fake_calls"].append(response_data)
        with open("testing/fake_openai_calls.json", "w") as f:
            json.dump(fake_calls, f, indent=2, default=str)

        return OpenAIResponse(response_data, status_code=status_code, raw_logging=self.raw_logging)
