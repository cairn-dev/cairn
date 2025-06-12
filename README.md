
<div align="center">

![Cairn Logo](static/docs/images/cairn-logo.png)

*Github-integrated background agents, automating end-to-end software engineering,  fully open source.*


[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104.1-009688.svg)](https://fastapi.tiangolo.com/)
[![Contributing](https://img.shields.io/badge/contributions-welcome-brightgreen.svg)](.github/CONTRIBUTING.md)

[🚀 Quick Start](#-quick-start) | [📖 Documentation](https://try-cairn.com) | <span style="margin-left:-5px">[<img src="https://logos-world.net/wp-content/uploads/2020/12/Discord-Emblem.png" alt="Discord" width="30" height="15" style="position:relative; top:3px; margin-right:-7px;"/> Discord](https://discord.gg/C67EdrKN)</span> | [🐛 Issues](https://github.com/cairn-dev/cairn/issues) | [🤝 Contributing](.github/CONTRIBUTING.md)

</div>


## 🪨 What is Cairn?

Cairn is a simple open-source background-agent system. Think [Codex](https://openai.com/index/introducing-codex/), [Jules](https://jules.google/), or [Cursor Background Agents](https://docs.cursor.com/background-agent), but open sourced!
You can run Cairn locally, connect it to your repos, use your favorite LLM and execute fullstack tasks, 100% in the background. Save time for the things you want to do, not the boring stuff!

### 🤖 Model Support

| Provider | Status | Models |
|----------|--------|---------|
| 🟢 **Anthropic** | ✅ Supported | Claude Sonnet 4, Claude Sonnet 3.7, Claude Sonnet 3.5 |
| 🟡 **OpenAI** | 🚧 Coming Soon |
| 🟡 **Gemini** | 🚧 Coming Soon |

## 🚀 Quick Start

### Installation

1. **Clone or fork the repository**
   ```bash
   git clone git@github.com:cairn-dev/cairn.git
   cd cairn
   ```

2. **Install dependencies**

   **Option A: Using venv (recommended)**
   ```bash
   python -m venv cairn-env
   source cairn-env/bin/activate  # On Windows: cairn-env\Scripts\activate
   pip install -r requirements.txt
   ```

   **Option B: Using conda**
   ```bash
   conda create -n cairn python=3.10
   conda activate cairn
   pip install -r requirements.txt
   ```

   **Option C: System-wide (not recommended)**
   ```bash
   pip install -r requirements.txt
   ```

3. **Set up github access**

   Cairn uses a self-hosted GitHub app (you own and control it) with read/write permissions to edit your repositories.

   ```
   Step 1: Create your GitHub App
   ```

   **For Personal Account:**
   1. Navigate to [GitHub Apps Settings](https://github.com/settings/apps)
   2. Click **"New GitHub App"**

   **For Organization:**
   1. Navigate to `https://github.com/organizations/YOUR-ORG-NAME/settings/apps`
   2. Replace `YOUR-ORG-NAME` with your actual organization name
   3. Click **"New GitHub App"**

   **App Configuration:**
   ```
   • App Name: "Cairn Agent for [Your-Username]" (must be globally unique)
   • Description: "Cairn automated development agent"
   • Homepage URL: https://try-cairn.com (or leave blank)
   • Webhook URL: Leave blank
   • Webhook Secret: Leave blank
   • ✅ Disable "Active" checkbox under Webhooks

   Repository Permissions:
   • Contents: Read & write ✅
   • Pull requests: Read & write ✅
   • Metadata: Read ✅ (auto-selected)

   Where can this GitHub App be installed?
   • Select "Only on this account" ✅
   ```

   4. Click **"Create GitHub App"** to finish

   ```
   Step 2: Gather your credentials (3 required values)
   ```
   ```
   • App ID: Copy from app settings page (displayed at top)
   • Private Key: Generate and download .pem file → save to cairn project root
   • Installation ID: Click "Install App" → Select repositories → Install
     Then check browser URL: https://github.com/settings/installations/[INSTALLATION_ID]
   ```

   ```
   Step 3: Note your credentials for .env and repos.json configuration
   ```
   You'll need these three values for your configuration:
   - **App ID**: Goes in your `.env` file.
   - **Private Key Path**: Path to your downloaded .pem file, goes in your `.env` file.
   - **Installation ID**: Goes in the `repos.json` file.

   ```
   ⚠️  Security: Keep your .pem file secure and never commit it to version control
   📖  Reference: https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app
   ```


3. **Configure environment variables and repositories**
   ```bash
   # Copy the example environment file
   cp .env.example .env
   ```

   Edit your `.env` file with the following configuration:
   ```bash
   # GitHub App credentials
   GITHUB_APP_ID=your_app_id_here
   GITHUB_PRIVATE_KEY_PATH=your_private_key_file.pem

   # Anthropic API key (REQUIRED) - Get from https://console.anthropic.com/
   ANTHROPIC_API_KEY=your_anthropic_api_key_here
   ANTHROPIC_MODEL_NAME=claude-sonnet-4-20250514
   ```

   Create a `repos.json` file in the root of the project with the following structure. Add an entry for each repository you want to connect.
    ```json
    {
      "connected_repos": [
        {
          "name": "your-repo-name",
          "owner": "your-github-username-or-org",
          "installation_id": 12345678
        },
        {
          "name": "another-repo",
          "owner": "your-github-username-or-org",
          "installation_id": 87654321
        }
      ]
    }
    ```
---

### 🏃 Running Cairn

#### Option 1 (recommended): Simple static HTML page.
```bash
python fastapi_app/app.py
```
Then, navigate to `http://0.0.0.0:8000` in your browser.

#### Option 2: Nicer next.js frontend.
```bash
COMING SOON
```

---

### Demo

![Cairn Demo](static/docs/images/demo1.gif)

### Your First Task

1. **Access the interface** (web or terminal)
2. **Select an agent type** (Fullstack Planner, PM, or SWE)
- SWE: recommended for simple self-contained subtasks. Output is a branch with the changes.
- PM: recommended for slightly more complex subtasks. Delegates software changes to SWE. Output is a PR.
- Fullstack Planner: recommended for fullstack or multi-step tasks. Output is a list of subtasks that can be ran in parallel, and who will communicate if necessary on cross-subtask code.
3. **Choose target repositories** from your connected repos
4. **Describe your task** in natural language
5. **Monitor progress** through real-time logs and status updates

---

## ⚙️ Cairn Settings & Memory

Cairn maintains local configuration and memory through a `.cairn/` directory that gets automatically created in your project root when you run tasks locally.

### Directory Structure

```
.cairn/
├── settings.json          # Global and repo-specific rules
└── memory/
    ├── repo1.json         # Memory for repo1
    ├── repo2.json         # Memory for repo2
    └── ...                # Additional repo memory files
```

### Settings Configuration

**`.cairn/settings.json`** allows you to define custom rules that agents must follow:

```json
{
    "general_rules": [
        "Always use TypeScript instead of JavaScript",
        "Follow the existing code style and patterns",
        "Add comprehensive error handling"
    ],
    "repo_specific_rules": {
        "my-frontend": [
            "Use React hooks instead of class components",
            "Follow the existing component structure in src/components/"
        ],
        "my-backend": [
            "Use FastAPI async patterns",
            "Always validate input parameters"
        ]
    }
}
```

### Repository Memory

**`.cairn/memory/<repo-name>.json`** stores persistent memory for each repository. This is generated by the agents. In practice, this saves money/time by skipping certain tool calls the agents use to figure out the structure of your repo.


---

## 🏗️ Architecture Overview

```mermaid
graph TB
    UI[Web Dashboard] --> API[FastAPI Backend]
    API --> WM[Worker Manager]
    WM --> FP["Fullstack Planner Agent<br/>(plans long running tasks,<br/>coordinates overall workflow)"]

    subgraph PM_LEVEL[" "]
        direction LR
        PM1["Project Manager 1<br/>(generates a full PR)"]
        PM2["Project Manager 2<br/>(generates a full PR)"]
        PM_MORE["..."]
    end

    FP --> PM1
    FP --> PM2
    FP --> PM_MORE

    PM1 --> SWE1["SWE1<br/>"]
    PM2 --> SWE2["SWE2<br/>"]

    SWE1 <-.->|a2a comms| SWE2

    WM --> DB[(SQLite Database)]
    WM --> LS[Log Storage]

    style PM_LEVEL fill:transparent,stroke:transparent
```

Cairn uses three simple (and hierarchical) agents to accomplish tasks. We call them `SWE`, `PM` and `FULLSTACK PLANNER`. The `SWE` is specialized at making code changes. The `PM` is specialized at delegating specific instructions to the `SWE`, checking the `SWE` changes, tracking dependencies, and creating a pull request. The `FULLSTACK PLANNER` is specialized at breaking down a large task into multiple subtasks that can run in parallel, and for orchestrating communication between any related parallel tasks.

For example, if you assign a task like "retrieve user metrics from our supabase backend, and display this in a chart in the frontend" to the `FULLSTACK PLANNER`, a single agent could implement both parts itself, but this is slower (and contextually worse) than having one agent implement the backend and another implement the frontend. We parallelize this process and allow the agents working on the frontend and backend to communicate with each other to ensure consistent API routes and data formats!

We use subprocess to spawn multiple parallel processes, SQLite as a persistent database, and FastAPI to provide a simple frontend.


## 🛠️ Development

### Project Structure

```
cairn/
├── agent_worker/              # Agent execution engine
│   ├── worker.py             # Main worker implementation
│   └── __main__.py           # CLI entry point
├── cairn_utils/              # Core utilities and libraries
│   ├── agents/               # Agent implementations, including prompts
│   ├── github_utils.py       # GitHub API integration
│   ├── toolbox.py           # Agent tools and capabilities
│   ├── task_storage.py      # Database operations
│   └── agent_classes.py     # Agent base classes
├── fastapi_app/              # Web interface
│   └── app.py               # FastAPI application
├── static/                   # Web UI assets
├── docs/                     # Documentation
├── examples/                 # Usage examples
├── .github/                  # GitHub templates and workflows
│   ├── ISSUE_TEMPLATE/       # Issue templates
│   └── pull_request_template.md # PR template
└── tests/                    # Test suite
```

### Contributing

We welcome contributions from the community! Please check our [Contributing Guide](.github/CONTRIBUTING.md) for more information on how to get started.

#### Issue Templates

We provide several issue templates to help you report bugs, request features, or suggest documentation improvements:

- [🐛 Bug Report](.github/ISSUE_TEMPLATE/bug_report.md)
- [💡 Feature Request](.github/ISSUE_TEMPLATE/feature_request.md)
- [📖 Documentation](.github/ISSUE_TEMPLATE/documentation.md)

#### Pull Request Template

When submitting a pull request, please use our [Pull Request Template](.github/pull_request_template.md) to ensure your contribution includes all the necessary information.

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🗺️ Roadmap

### Current Version (v0.1.0)
- ✅ Multi-agent task execution
- ✅ GitHub integration
- ✅ Simple web interface

### Coming Soon
- 🌐 OpenAI & Gemini support
- 🖥️ Agent-runnable code environments
- ▶️ Pausable, playable, restartable tasks
- 🎯 Custom diff-application models
- 🔍 Embedding search support (auto-updating with github webhooks)
- and more...


<!--
<div align="center">

[![Star History Chart](https://api.star-history.com/svg?repos=cairn-dev/cairn&type=Date)](https://www.star-history.com/#cairn-dev/cairn&Date)

</div> -->

---

<div align="center">

**[⭐ Star us on GitHub](https://github.com/cairn-dev/cairn)** • **[🍴 Fork the project](https://github.com/cairn-dev/cairn/fork)** • **[📝 Contribute](.github/CONTRIBUTING.md)** • **[🐛 Report Bug](.github/ISSUE_TEMPLATE/bug_report.md)** • **[💡 Request Feature](.github/ISSUE_TEMPLATE/feature_request.md)**

Made with ❤️ by the Cairn team and contributors

</div>
