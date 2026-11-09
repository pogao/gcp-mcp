# gcp-mcp

gcp-mcp is a read-only [Model Context Protocol](https://modelcontextprotocol.io) server for Google Cloud. It speaks MCP protocol version `2026-07-28` over stdio and stateless Streamable HTTP, and it lets a model read any of the 194 Google Cloud services in the discovery documents that ship with `google-api-python-client`. It cannot create, change, or delete anything.

## What you get

The server has seven tools. The first four reach every service. A model uses them in order to find a service, find a read method, learn its arguments, and call it. The last three answer a security question with a fixed rule.

| Tool | Question it answers | Example arguments |
| --- | --- | --- |
| `gcp_services(query?)` | Which services exist, their default version, and how many read methods each has. | `{"query": "kubernetes"}` |
| `gcp_methods(service, query?, version?)` | Which read methods a service has, grouped by resource. | `{"service": "compute", "query": "instances"}` |
| `gcp_describe_method(service, method, version?)` | Which parameters and request body a method takes, what it returns, and how it pages. | `{"service": "compute", "method": "instances.get"}` |
| `gcp_call(service, method, args?, body?, version?)` | What the method returns, as JSON. | `{"service": "compute", "method": "instances.aggregatedList", "args": {"project": "example-project"}}` |
| `unsafe_ssh_exposure(project)` | Which enabled ingress firewall rules allow TCP port 22 from `0.0.0.0/0` or `::/0`. | `{"project": "example-project"}` |
| `find_project_owners(project)` | Which members hold `roles/owner` on the project. | `{"project": "example-project"}` |
| `is_bucket_public(bucket)` | Whether the bucket's IAM policy grants a role to `allUsers` or `allAuthenticatedUsers`. | `{"bucket": "example-bucket"}` |

`tests/golden/catalog.json` lists the services, and `tests/golden/read_methods.txt` lists the 4951 read methods in their default versions.

`gcp_call` takes path and query parameters in `args`, under the API's own parameter names, and the request body of a POST read in `body`. A call returns the response with paging and size details:

```jsonc
gcp_call({"service": "compute", "method": "instances.aggregatedList",
          "args": {"project": "example-project",
                   "fields": "items.*.instances(name,networkInterfaces(accessConfigs(natIP))),nextPageToken"}})
// {"service": "compute", "version": "v1", "method": "instances.aggregatedList",
//  "response": {"items": {...}, "nextPageToken": "..."},
//  "next_page_token": "...", "truncated": false, "omitted_items": 0, "redacted_fields": 0,
//  "hint": "More results exist, so call again with pageToken set to next_page_token in args."}
```

- One call returns one page. To get the next page, pass `next_page_token` as `pageToken` in `args`, or in `body` for a POST read.
- The server cuts a response to `--max-response-bytes`, 100000 by default. When it drops list items, `truncated` is `true` and `omitted_items` counts them. No later page returns them, so repeat the call with `fields` or a smaller page size.
- `fields` takes the Google Cloud partial response syntax, such as `items(name,status),nextPageToken`.
- The value of every field whose name looks like a credential, such as `password` or `privateKey`, arrives as `"[redacted]"`, and `redacted_fields` counts them.
- `gcp_call` refuses a mutating method, such as `instances.delete`, with the reason and sends no request.

## Run the server

You need Python 3.13, [`uv`](https://docs.astral.sh/uv/), and the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install). The server calls Google Cloud with Application Default Credentials, so log in first:

```bash
git clone https://github.com/pogao/gcp-mcp.git
cd gcp-mcp
uv sync
gcloud auth application-default login
```

The server calls Google Cloud with the Application Default Credentials of whoever starts it. Start it under an identity that can only read, such as a dedicated service account with the Viewer role or narrower roles. To use that account from your own login, run `gcloud auth application-default login --impersonate-service-account viewer@example-project.iam.gserviceaccount.com`, or run the server as the service account. The read-only policy is the first line of defense and IAM is the second. Redaction cannot catch a secret stored as a data value, so a Viewer identity also limits what a prompt-injected model can read.

To serve over stdio, the default, run:

```bash
uv run gcp-mcp
```

To serve over Streamable HTTP at `http://127.0.0.1:8888/mcp`, run:

```bash
uv run gcp-mcp --transport http --port 8888
```

Every setting is an environment variable with the prefix `GCP_MCP_`. Every setting except the PEM public key is also a flag, and a flag wins over the environment. To do the same HTTP run with environment variables, run:

```bash
GCP_MCP_TRANSPORT=http GCP_MCP_PORT=8888 uv run gcp-mcp
```

| Flag | Environment variable | Default | Meaning |
| --- | --- | --- | --- |
| `--transport` | `GCP_MCP_TRANSPORT` | `stdio` | `stdio` or `http`. |
| `--host` | `GCP_MCP_HOST` | `127.0.0.1` | The address the HTTP transport listens on. |
| `--port` | `GCP_MCP_PORT` | `8888` | The port the HTTP transport listens on. |
| `--max-response-bytes` | `GCP_MCP_MAX_RESPONSE_BYTES` | `100000` | The size limit for a Google Cloud response, in bytes of compact JSON. |
| `--default-page-size` | `GCP_MCP_DEFAULT_PAGE_SIZE` | `100` | The page size the server sends when a call sets none, up to the method's maximum. |
| `--request-timeout-s` | `GCP_MCP_REQUEST_TIMEOUT_S` | `60` | Seconds to wait for Google Cloud. |
| `--log-level` | `GCP_MCP_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, or `ERROR`. |

`GET /healthz` returns `OK`. Logs are JSON lines on stderr. The HTTP client logs only warnings and errors, because its request lines carry project ids. Run `uv run gcp-mcp --help` to list every flag.

Without `--require-auth`, the HTTP transport accepts every request, and the server warns at startup when `--host` is not a loopback address. Keep `--host` on a loopback address in that case.

To connect Claude Desktop, add the server to the `mcpServers` object of its configuration file. For Claude Code, add it to `.mcp.json`. Replace `/path/to/gcp-mcp` with the directory that holds this repository.

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

## Require authentication

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

To admit only named callers, list them in `--auth-allowed-subjects`. The server then accepts a token only when its `sub` claim equals one of the entries, or when its `email` claim equals one and `email_verified` is `true`. The match is exact and case-sensitive.

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

`gcloud auth print-identity-token` prints a token for the active account unless you impersonate a service account, as above. `--audiences` takes one audience. With `--include-email`, the token carries the service account's `email` claim, so the allowlist entry can be the email. Without it, list the service account's unique ID, which is the token's `sub` claim. An `email` entry matches only when the token also carries `email_verified` set to `true`. With an issuer that does not set that claim, list `sub` values.

| Flag | Environment variable | Meaning |
| --- | --- | --- |
| `--require-auth` | `GCP_MCP_REQUIRE_AUTH=true` | Require a valid bearer token on every MCP request. Needs `--transport http`. |
| `--base-url` | `GCP_MCP_BASE_URL` | The public URL of the server, without `/mcp`. |
| `--auth-issuer` | `GCP_MCP_AUTH_ISSUER` | The `iss` claim that every token must carry. |
| `--auth-jwks-uri` | `GCP_MCP_AUTH_JWKS_URI` | The issuer's JSON Web Key Set. Set this or a public key, not both. |
| Environment only | `GCP_MCP_AUTH_PUBLIC_KEY` | The issuer's RSA public key as PEM text. The command line cannot carry PEM text. |
| `--auth-audience` | `GCP_MCP_AUTH_AUDIENCE` | The `aud` claim that every token must carry. The default is the base URL plus `/mcp`. |
| `--auth-required-scopes` | `GCP_MCP_AUTH_REQUIRED_SCOPES` | Scopes that every token must carry, separated by commas. Each scope is an RFC 6749 scope token, so no spaces, quotes, or JSON. |
| `--auth-allowed-subjects` | `GCP_MCP_AUTH_ALLOWED_SUBJECTS` | Callers to admit, separated by commas. A token passes when its `sub` claim equals an entry, or when its `email` claim equals an entry and `email_verified` is `true`. Empty admits every subject the issuer signs for. |

With `--require-auth`, the server does the following:

- It answers a `POST` to `/mcp` without a valid token with `401` and a `WWW-Authenticate` header that points to `https://mcp.example.com/.well-known/oauth-protected-resource/mcp`. `GET /healthz` needs no token.
- It serves that RFC 9728 metadata document. The document names the issuer as the authorization server and `https://mcp.example.com/mcp` as the resource.
- It accepts a token only when the signature is valid, `iss` and `aud` match, the required scopes are present, `exp` is in the future, and `nbf`, if present, is not. A token without `exp` is refused, and so is one whose `iat` is more than five minutes in the future.
- With `--auth-allowed-subjects`, it also refuses a token whose `sub` and `email` claims are both outside the list, and one whose `email` is in the list but whose `email_verified` claim is not `true`.
- A token without a required scope gets `401`, not `403`, in fastmcp 4.1.6.
- It logs each refusal of its own as one JSON line at INFO. Those are a missing `exp`, an `nbf` or `iat` too far ahead, a subject outside the list, and an allowed `email` without `email_verified`. The line names the reason and the `sub` and `email` claims, never the token. fastmcp logs the refusals it makes, such as a bad signature or a wrong audience.

If a setting is missing or does not fit, `gcp-mcp` exits with code 2 and names the setting on stderr.

The server uses a client token only to decide whether to serve the request. It calls Google Cloud with its own Application Default Credentials and never forwards a client token to Google.

The MCP specification says that a stdio server should not use protocol authentication. Over stdio, the server runs with the credentials of the operator who starts it, so `--require-auth` with stdio exits with code 2.

## How it stays read-only

The server calls a Google API method only when `admit` in `gcp_mcp/policy.py` judges it a read, and it refuses every other method with a reason before it sends anything. A GET needs a read verb such as `get` or `list`, because Google ships GET methods that mutate, and a POST needs a read name such as `getIamPolicy` or `runQuery`. One function in `gcp_mcp/rest.py` builds every request and refuses path values, parameters, and body keys that could reach another endpoint or open a transaction. The server replaces credential-shaped response fields with `"[redacted]"`. Tests run the policy over all 604 bundled discovery documents and pin the result in golden files, so every change to the read set is a reviewed diff.

[How gcp-mcp stays read-only](docs/design.md) explains each rule and how the tests prove it. To report a way around it, follow [the security policy](SECURITY.md).

## Known limits

The guarantee has limits that follow from its design, such as resource names with a letters-only `:` suffix, excluded inference endpoints, and redaction by field name. [Known limits](docs/design.md#known-limits) lists each one with its reason.

## Upgrade from 0.1

0.2 removes twelve of the fifteen tools of 0.1, among them `list_gcs_buckets`, `describe_gcp_instance`, `list_firewall_rules`, and `list_project_iam`. `gcp_call` serves the API method behind each one and returns the full response. The four most used map as follows:

| 0.1 tool | `gcp_call` arguments |
| --- | --- |
| `list_gcs_buckets(project_id)` | `{"service": "storage", "method": "buckets.list", "args": {"project": "example-project"}}` |
| `describe_gcp_instance(instance_name, project_id, zone)` | `{"service": "compute", "method": "instances.get", "args": {"project": "example-project", "zone": "us-central1-a", "instance": "example-instance"}}` |
| `list_firewall_rules(project_id)` | `{"service": "compute", "method": "firewalls.list", "args": {"project": "example-project"}}` |
| `list_project_iam(project_id)` | `{"service": "cloudresourcemanager", "method": "projects.getIamPolicy", "args": {"resource": "projects/example-project"}}` |

To find the method behind another removed tool, call `gcp_methods` with the service name.

`fastmcp run main.py:mcp` is gone. Run `uv run gcp-mcp` instead, and point the `command` and `args` of your MCP client at it as shown in [Run the server](#run-the-server).

`unsafe_ssh_exposure`, `find_project_owners`, and `is_bucket_public` keep their names. They take `project` or `bucket` instead of `project_id` and `bucket_name`, and each returns an object instead of a bare list or boolean. `unsafe_ssh_exposure` and `find_project_owners` carry a `count`, and `is_bucket_public` carries `public` and the public `bindings`.

## Development

To install the development tools and run the checks that CI runs:

```bash
uv sync --group dev
uv run ruff check .
uv run pytest -q
```

The tests never open a network connection. Three golden files under `tests/golden/` pin the policy verdicts, the service catalog, and the read methods. When a test reports a golden diff, review it, then regenerate the files:

```bash
uv run python -m scripts.golden --check
uv run python -m scripts.golden --write
```

The catalog comes from the discovery documents in the installed `google-api-python-client`, so new Google Cloud services arrive with a new version of that package. Dependabot checks for that upgrade every week and opens it as a pull request. That pull request turns the golden tests red on purpose, because the goldens still describe the old corpus. Check out the Dependabot branch, run `uv run python -m scripts.golden --write`, and review the diff. The diff of `tests/golden/policy.json` shows each POST that the policy newly admits and each GET that it newly refuses. The diff of `tests/golden/read_methods.txt` shows each method that appears or disappears. Commit the goldens to the pull request. That diff is the security review of each catalog update. To upgrade by hand:

1. Run `uv lock --upgrade-package google-api-python-client`.
2. Run `uv sync --group dev`.
3. Run `uv run python -m scripts.golden --check` and review the diff, especially new admitted POSTs and new services.
4. Run `uv run python -m scripts.golden --write` and `uv run pytest -q`.
5. Commit `uv.lock` and `tests/golden/` together.
