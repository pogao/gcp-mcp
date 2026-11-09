# Security policy

gcp-mcp promises that a model can read Google Cloud through it and change nothing. A break in that promise is a security issue. [How gcp-mcp stays read-only](docs/design.md) explains the guarantee and lists its known limits.

## Report a vulnerability

Report it privately through GitHub. On the repository's **Security** tab, click **Report a vulnerability**. Do not open a public issue or pull request for it.

Include the method id or request that shows the problem, the version or commit you tested, and what you expected the server to do. Use made-up project and bucket names, and never include a real token or key.

## Supported versions

Only `main` gets security fixes.

## What counts as a security issue

- An admitted method that creates, changes, or deletes anything, or starts work such as a job, an export, or a long-running operation.
- A path from any tool to an endpoint that the policy does not admit, such as an argument that rewrites the URL or the request body.
- A credential or secret in a tool response.
- A token that the server accepts although the README says that the server refuses it.

A behavior that [the known limits](docs/design.md#known-limits) already describe is not a vulnerability. If you can show that one of those limits does more harm than the page says, report it.
