# 💻 Running LangGraph Studio via CLI

If you prefer using the command line or want to run the studio without the full Desktop app, you can use the `langgraph-cli`.

## 1. Installation

First, ensure you have the LangGraph CLI installed in your environment:

```bash
pip install "langgraph-cli[draw]"
```

## 2. Setup Environment

Ensure you are in the `ai-service` directory and have your dependencies installed:

```bash
cd ai-service
poetry install
```

Make sure your `.env` file is present with the required keys (e.g., `GOOGLE_API_KEY`).

## 3. Launching the Studio

Run the following command to start the LangGraph development server. This will provide a local URL to access the Studio interface in your browser.

```bash
langgraph dev
```

> [!TIP]
> This command will automatically detect the `langgraph.json` configuration in the current directory.

## 4. Useful CLI Commands

| Command | Description |
| :--- | :--- |
| `langgraph dev` | Start the development server with hot-reload. |
| `langgraph build` | Build the graph for production. |
| `langgraph --help` | Show all available commands. |

## 5. Input Example

When the Studio opens, you can use the following JSON for a quick test:

```json
{
  "job_id": "cli-test-001",
  "raw_text": "The system shall allow users to browse products without logging in.",
  "file_type": "pdf"
}
```
