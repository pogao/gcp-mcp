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

#### Cloud IAM

- **List Project IAM Policy**: Retrieves the full Identity and Access Management (IAM) policy for a specified project.
- **🛡️ Find Project Owners**: A security-focused tool that finds principals with the highly-privileged `roles/owner` basic role assigned to them.

#### Cloud Storage

- **List GCS Buckets**: Retrieve a list of all Google Cloud Storage buckets in a project.
- **Describe GCS Bucket**: Get detailed metadata for a specific storage bucket.
- **Check UBLA**: Verify if a bucket has Uniform Bucket-Level Access (UBLA) enabled.
- **🛡️ Public Bucket Analysis**: A security-focused tool that checks if a bucket is publicly accessible to the internet.

## Technology Stack

- **Backend**: Python 3
- **Framework**: FastAPI & `fastmcp`
- **Cloud SDK**: `google-cloud-compute`, `google-cloud-storage` and `google-cloud-resource-manager` for interacting with the GCP API.
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

### Run the server

The `gcp-mcp` command builds the server from its settings and serves it. Every setting is a flag and an environment variable with the prefix `GCP_MCP_`. A flag wins over an environment variable.

To serve over stdio, the default, run:

```bash
uv run gcp-mcp
```

To serve over Streamable HTTP at `http://127.0.0.1:8888/mcp`, run:

```bash
uv run gcp-mcp --transport http --port 8888
```

The same run with environment variables:

```bash
GCP_MCP_TRANSPORT=http GCP_MCP_PORT=8888 uv run gcp-mcp
```

`GET /healthz` returns `OK`. Logs are JSON lines on stderr. Run `uv run gcp-mcp --help` to list every flag.

Without `--require-auth`, the HTTP transport accepts every request. Keep `--host` on a loopback address in that case.

#### Require authentication

To make the HTTP transport require a bearer token, add `--require-auth` and name the token issuer and its keys:

```bash
uv run gcp-mcp --transport http --host 0.0.0.0 --port 8888 \
	--require-auth \
	--base-url https://mcp.example.com \
	--auth-issuer https://issuer.example.com \
	--auth-jwks-uri https://issuer.example.com/.well-known/jwks.json \
	--auth-required-scopes gcp:read
```

Any OAuth 2.1 or OpenID Connect issuer that signs RS256 JWTs works. The server admits every token that the issuer signs for this audience, so the issuer decides who gets in. Use an issuer that you control and that gives such tokens only to clients you approve.

Do not use Google as the issuer. Google signs an ID token for any service account in any project, with any audience the caller names. With `--auth-issuer https://accounts.google.com`, any Google service account could call this server and read your Google Cloud through it.

| Flag | Environment variable | Meaning |
| --- | --- | --- |
| `--require-auth` | `GCP_MCP_REQUIRE_AUTH=true` | Require a valid bearer token on every MCP request. Needs `--transport http`. |
| `--base-url` | `GCP_MCP_BASE_URL` | The public URL of the server, without `/mcp`. |
| `--auth-issuer` | `GCP_MCP_AUTH_ISSUER` | The `iss` claim that every token must carry. |
| `--auth-jwks-uri` | `GCP_MCP_AUTH_JWKS_URI` | The issuer's JSON Web Key Set. Set this or a public key, not both. |
| Environment only | `GCP_MCP_AUTH_PUBLIC_KEY` | The issuer's RSA public key as PEM text. The command line cannot carry PEM text. |
| `--auth-audience` | `GCP_MCP_AUTH_AUDIENCE` | The `aud` claim that every token must carry. The default is the base URL plus `/mcp`. |
| `--auth-required-scopes` | `GCP_MCP_AUTH_REQUIRED_SCOPES` | Scopes that every token must carry, separated by commas. |

With `--require-auth`, the server does the following:

- It answers a `POST` to `/mcp` without a valid token with `401` and a `WWW-Authenticate` header that points to `https://mcp.example.com/.well-known/oauth-protected-resource/mcp`. `GET /healthz` needs no token.
- It serves that RFC 9728 metadata document. The document names the issuer as the authorization server and `https://mcp.example.com/mcp` as the resource.
- It accepts a token only when the signature is valid, `iss` and `aud` match, the required scopes are present, `exp` is in the future, and `nbf`, if present, is not. A token without `exp` is refused, and so is one whose `iat` is more than five minutes in the future.
- A token without a required scope gets `401`, not `403`, in fastmcp 4.1.6.

If a setting is missing or does not fit, `gcp-mcp` exits with code 2 and names the setting on stderr.

The server uses a client token only to decide whether to serve the request. It calls Google Cloud with its own Application Default Credentials and never forwards a client token to Google.

The MCP specification says that a stdio server should not use protocol authentication. Over stdio, the server runs with the credentials of the operator who starts it, so `--require-auth` with stdio exits with code 2.

### Connect Claude Desktop or Claude Code

Add the server to the `mcpServers` object of the Claude Desktop configuration file, or to `.mcp.json` for Claude Code. Replace `/path/to/gcp-mcp` with the directory that holds this repository.

```json
{
  "mcpServers": {
    "gcp": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/gcp-mcp", "gcp-mcp"]
    }
  }
}
```

The legacy command `uv run fastmcp run main.py:mcp` still works until the next pull requests replace the tools.
