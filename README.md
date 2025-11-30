# GCP MCP Server

![Status: Work in Progress](https://img.shields.io/badge/status-work%20in%20progress-yellow.svg)

This project is a Python-based server built with `fastmcp` that exposes a set of tools to a Large Language Model (LLM). The primary goal is to enable an LLM to safely query and analyze a Google Cloud Platform (GCP) environment, turning natural language questions into actionable cloud insights.

This project is under active development and serves as a living demonstration of cloud integration, API design, and professional coding practices for my portfolio.

## Key Features

The server currently provides the following tools to an LLM:

### ☁️ Google Cloud Platform Tools

#### Compute Engine

- **List Instances**: List all VM instances within a specific project and zone.
- **Describe Instance**: Get detailed information about a specific VM instance.

#### VPC Networking & Firewalls

- **List All Firewall Rules**: Retrieve every firewall rule within a given project.
- **List Firewall Rules per VPC**: Filter firewall rules for a specific VPC network.
- **Describe Firewall Rule**: Get detailed information about a single, named firewall rule.
- **🛡️ Unsafe SSH Exposure Analysis**: A security-focused tool that actively scans for firewall rules that dangerously expose SSH (port 22) to the entire internet (`0.0.0.0/0`).

## Technology Stack

- **Backend**: Python 3
- **Framework**: FastAPI & `fastmcp`
- **Cloud SDK**: `google-cloud-compute` for interacting with the GCP API.
- **Logging**: Structured JSON logging implemented with `structlog`.
- **Code Quality**:
  - Centralized exception handling using Python decorators.

## Getting Started

### Prerequisites

- Python 3.10+
- `uv` (or `pip`) for package management.
- [Google Cloud SDK](https://cloud.google.com/sdk/docs/install) installed and configured on your local machine.

### Installation & Running

1.  **Clone the repository:**

    ```bash
    git clone <repository-url>
    cd gcp-mcp
    ```

2.  **Set up the virtual environment and install dependencies:**

    ```bash
    uv sync
    ```

3.  **Authenticate with GCP:**
    This is crucial. The application uses Application Default Credentials (ADC) to authenticate with Google Cloud.

    ```bash
    gcloud auth application-default login
    ```

4.  **Run the server:**

    **Development Mode (with MCP Inspector):**
    ```bash
    uv run fastmcp dev main.py:mcp
    ```

    **Production / Stdio Mode:**
    ```bash
    uv run fastmcp run main.py:mcp
    ```
