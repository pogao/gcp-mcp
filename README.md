# GCP MCP Server

![Status: Work in Progress](https://img.shields.io/badge/status-work%20in%20progress-yellow.svg)

This project is a Python-based server built with `fastmcp` that exposes a set of tools to a Large Language Model (LLM). The primary goal is to enable an LLM to safely query and analyze a Google Cloud Platform (GCP) environment, turning natural language questions into actionable cloud insights.

This project is under active development and serves as a living demonstration of cloud integration, API design, and professional coding practices for my portfolio.

## Tools

Four tools cover every Google Cloud service in the discovery documents that ship with `google-api-python-client`. That is 194 services and 4,951 read methods, listed in `tests/golden/catalog.json` and `tests/golden/read_methods.txt`. A model uses the tools in this order:

1. `gcp_services` finds the service.
2. `gcp_methods` finds the read method.
3. `gcp_describe_method` shows the arguments of the method.
4. `gcp_call` runs the method.

| Tool | Question it answers |
| --- | --- |
| `gcp_services(query?)` | Which services exist, what their default version is, and how many read methods each has. |
| `gcp_methods(service, query?, version?)` | Which read methods a service has, grouped by resource. |
| `gcp_describe_method(service, method, version?)` | Which parameters and request body a method takes, what it returns, and how it pages. |
| `gcp_call(service, method, args?, body?, version?)` | What the method returns, as JSON. |

Find the service:

```jsonc
gcp_services({"query": "kubernetes"})
// {"services": [{"name": "container", "title": "Kubernetes Engine API",
//   "default_version": "v1", "versions": ["v1", "v1beta1"], "read_methods": 22,
//   "docs_url": "https://cloud.google.com/kubernetes-engine/docs/"}], "count": 1}
```

Find the method. Join a resource and a method name with a dot to get `instances.aggregatedList`. Methods on the service itself sit under the empty resource `""`.

```jsonc
gcp_methods({"service": "compute", "query": "instances"})
// {"service": "compute", "version": "v1",
//  "resources": {"instances": ["aggregatedList", "get", "getEffectiveFirewalls", ...,
//                              "list", "listReferrers", "testIamPermissions"], ...},
//  "read_methods": 459, "excluded_methods": 560}
```

Learn the arguments:

```jsonc
gcp_describe_method({"service": "compute", "method": "instances.get"})
// {"service": "compute", "version": "v1", "method": "instances.get",
//  "description": "Returns the specified Instance resource.",
//  "http_method": "GET", "path": "projects/{project}/zones/{zone}/instances/{instance}",
//  "params": [{"name": "instance", "location": "path", "type": "string",
//              "required": true, "repeated": false, "enum": null, "description": "..."}, ...],
//  "body": null, "response_schema": "Instance", "response_properties": {...},
//  "pagination": null, "deprecated": false, "docs_url": "https://cloud.google.com/compute/"}
```

Call it. `args` holds the path and query parameters under the API's own parameter names. `body` holds the request body of a POST read, such as `logging` `entries.list`.

```jsonc
gcp_call({"service": "compute", "method": "instances.aggregatedList",
          "args": {"project": "example-project",
                   "fields": "items.*.instances(name,networkInterfaces(accessConfigs(natIP))),nextPageToken"}})
// {"service": "compute", "version": "v1", "method": "instances.aggregatedList",
//  "response": {"items": {...}, "nextPageToken": "..."},
//  "next_page_token": "...", "truncated": false, "omitted_items": 0, "redacted_fields": 0,
//  "hint": "More results exist, so call again with pageToken set to next_page_token in args."}
```

The server sends that call as one `GET` to `compute.googleapis.com` with `maxResults=100`, the default page size, because the call did not set one.

### Keep responses small and complete

- A response is cut to `--max-response-bytes`, 100000 by default. When the server drops list items to fit, `truncated` is `true` and `omitted_items` counts them. A later page does not return the dropped items, so repeat the call with `fields` or a smaller page size.
- `fields` takes the Google Cloud partial response syntax, such as `items(name,status),nextPageToken`.
- One call returns one page. When `next_page_token` is set, pass it as `pageToken` in `args`, or in `body` for a POST read, to get the next page.
- The server replaces the value of every field whose name looks like a credential, such as `password` or `privateKey`, with `"[redacted]"`, and counts the replacements in `redacted_fields`.

### What the server refuses

The server cannot create, change, or delete anything. `gcp_methods` lists only read methods. `gcp_call` refuses a mutating method, such as `instances.delete`, sends no request, and names the reason. An error tells the model what to change. When the server's own credentials are missing or expired, the error says that the operator must run `gcloud auth application-default login`.

The server still serves the 15 legacy tools, such as `list_gcs_buckets` and `unsafe_ssh_exposure`, after the four generic tools. The next pull request replaces them.

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

Any OAuth 2.1 or OpenID Connect issuer that signs RS256 JWTs works. Without `--auth-allowed-subjects`, the server admits every token that the issuer signs for this audience, so the issuer decides who gets in. Use an issuer that you control and that gives such tokens only to clients you approve.

To admit only named callers, list them in `--auth-allowed-subjects`. The server then accepts a token only when its `sub` claim or its `email` claim equals one of the entries. The match is exact and case-sensitive.

Google signs an ID token for any service account in any project, with any audience the caller names. Without the allowlist, do not use Google as the issuer, because any Google service account could call this server and read your Google Cloud through it. With the allowlist, Google can be the issuer. Name the service accounts that may call the server:

```bash
uv run gcp-mcp --transport http --host 0.0.0.0 --port 8888 \
	--require-auth \
	--base-url https://mcp.example.com \
	--auth-issuer https://accounts.google.com \
	--auth-jwks-uri https://www.googleapis.com/oauth2/v3/certs \
	--auth-audience https://mcp.example.com/mcp \
	--auth-allowed-subjects reader@example-project.iam.gserviceaccount.com
```

On the client side, get a token for that audience as the service account:

```bash
gcloud auth print-identity-token \
	--impersonate-service-account=reader@example-project.iam.gserviceaccount.com \
	--audiences=https://mcp.example.com/mcp \
	--include-email
```

`gcloud auth print-identity-token` prints a token for the active account unless you impersonate a service account, as above. `--audiences` takes one audience. With `--include-email`, the token carries the service account's `email` claim, so the allowlist entry can be the email. Without it, list the service account's unique ID, which is the token's `sub` claim. The server matches `email` without checking `email_verified`, so with an issuer other than Google, list `sub` values.

| Flag | Environment variable | Meaning |
| --- | --- | --- |
| `--require-auth` | `GCP_MCP_REQUIRE_AUTH=true` | Require a valid bearer token on every MCP request. Needs `--transport http`. |
| `--base-url` | `GCP_MCP_BASE_URL` | The public URL of the server, without `/mcp`. |
| `--auth-issuer` | `GCP_MCP_AUTH_ISSUER` | The `iss` claim that every token must carry. |
| `--auth-jwks-uri` | `GCP_MCP_AUTH_JWKS_URI` | The issuer's JSON Web Key Set. Set this or a public key, not both. |
| Environment only | `GCP_MCP_AUTH_PUBLIC_KEY` | The issuer's RSA public key as PEM text. The command line cannot carry PEM text. |
| `--auth-audience` | `GCP_MCP_AUTH_AUDIENCE` | The `aud` claim that every token must carry. The default is the base URL plus `/mcp`. |
| `--auth-required-scopes` | `GCP_MCP_AUTH_REQUIRED_SCOPES` | Scopes that every token must carry, separated by commas. Each scope is an RFC 6749 scope token, so no spaces, quotes, or JSON. |
| `--auth-allowed-subjects` | `GCP_MCP_AUTH_ALLOWED_SUBJECTS` | Callers to admit, separated by commas. A token passes when its `sub` or `email` claim equals an entry. Empty admits every subject the issuer signs for. |

With `--require-auth`, the server does the following:

- It answers a `POST` to `/mcp` without a valid token with `401` and a `WWW-Authenticate` header that points to `https://mcp.example.com/.well-known/oauth-protected-resource/mcp`. `GET /healthz` needs no token.
- It serves that RFC 9728 metadata document. The document names the issuer as the authorization server and `https://mcp.example.com/mcp` as the resource.
- It accepts a token only when the signature is valid, `iss` and `aud` match, the required scopes are present, `exp` is in the future, and `nbf`, if present, is not. A token without `exp` is refused, and so is one whose `iat` is more than five minutes in the future.
- With `--auth-allowed-subjects`, it also refuses a token whose `sub` and `email` claims are both outside the list.
- A token without a required scope gets `401`, not `403`, in fastmcp 4.1.6.
- It logs each refusal of its own as one JSON line at INFO. Those are a missing `exp`, an `nbf` or `iat` too far ahead, and a subject outside the list. The line names the reason and the `sub` and `email` claims, never the token. fastmcp logs the refusals it makes, such as a bad signature or a wrong audience.

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
