# How gcp-mcp stays read-only

gcp-mcp lets a model read any Google Cloud service and change none of them. This page explains how it decides what a read is and where that guarantee ends.

## A policy, because a list cannot keep up

`google-api-python-client` 2.201.0 bundles 604 discovery documents with 27,829 methods. 391 documents describe Google Cloud, and they group into the 194 services in [`tests/golden/catalog.json`](../tests/golden/catalog.json). Nobody can review them by hand, and each release adds more. So `admit` in [`gcp_mcp/policy.py`](../gcp_mcp/policy.py) judges each method by its HTTP method, its id, whether it takes a body, and its response schema. It returns `Admitted`, or `Excluded` with a reason. It is pure and imports only the standard library, so a test can run it over every method offline.

## The rules, first match wins

1. DELETE, PATCH, and PUT mutate.
2. `DENY` and `DENY_PATTERNS` name methods that look like reads but are not, such as `bigquery.jobs.query`, which runs any SQL.
3. `SENSITIVE` and `SENSITIVE_IDS` name reads that return a credential or a secret.
4. A method that returns a long-running operation starts work, unless it is a GET on an `*operations` resource, which polls existing work.
5. A GET needs a leading verb in `READ_VERBS` or a name in `READ_IDIOMS`.
6. A POST needs a name in `READ_NAMES` or a leading verb in `POST_READ_VERBS`.

`admit` excludes anything else. The credential rule runs before the verb rules, so `getAuthString` never reaches the `get` check.

## GET is not a promise of a read

The corpus ships GET methods named `restore`, `move`, `cancel`, `restart`, and `subscribe`, such as `cloudbilling.organizations.billingAccounts.move`. A denylist would need every such verb, including the ones a later release adds. So rule 5 denies by default. A GET must start with a read verb such as `get` or `list`, or carry a known name such as `aggregatedList`. 264 GET methods fail, and [`tests/golden/policy.json`](../tests/golden/policy.json) lists each one.

## POST reads are real

Many reads take arguments that do not fit in a URL. `cloudresourcemanager.projects.getIamPolicy`, `logging.entries.list`, and Firestore `runQuery` are all POST, so a GET-only server would miss IAM policies and logs. Rule 6 admits a POST by a read name such as `getIamPolicy`, or by a leading verb such as `search`, after rule 4 has passed it. 594 POST methods pass, and `policy.json` lists them too.

## Credentials stay out, by name and by field

`SENSITIVE` matches whole camelCase words at the end of the method name, such as `password`, `secret`, or `token`. Whole words keep `getPasswordPolicy` admitted. `SENSITIVE_IDS` names methods whose purpose is a credential, such as Secret Manager `versions.access`, Redis auth tokens, and Android enrollment tokens.

Ordinary reads also return secrets beside the state they describe. `container.projects.locations.clusters.get` can return `masterAuth.password`. So the executor walks every response and replaces the value of each key that `REDACTED_FIELDS` matches with `"[redacted]"`. The pattern matches key endings such as `password`, `clientKey`, or `accessToken` in any case, and never a cursor such as `nextPageToken`. A string, object, or list under a matching key goes whole. A number or boolean stays, because those are flags such as `allowCredentials`.

## One catalog from the bundled documents

[`gcp_mcp/discovery.py`](../gcp_mcp/discovery.py) reads the `.json` files in `googleapiclient.discovery_cache.documents`, never their stale `index.json`. `is_gcp` keeps a document that offers the `cloud-platform` scope or links to docs on `cloud.google.com`, minus the Workspace, Maps, Play, and Firebase names in `NOT_GCP`.

Each service gets one default version. The highest band with reads wins, with GA above beta, alpha, and preview in that order. Inside the band, the version with the most admitted reads wins, and a tie goes to the higher number. Three tools take `version` to read another bundled version under the same policy.

`parse_service` is the only code that constructs a `ReadMethod`, and only for an admitted method. Holding one proves that `admit` said yes. The executor accepts nothing else.

## One request builder, with guards

In [`gcp_mcp/rest.py`](../gcp_mcp/rest.py), `prepare` is the only code that constructs a `PreparedRequest`. It refuses a bad request before anything reaches the network.

**Path values.** A simple `{x}` value is percent-encoded with no safe characters and cannot be empty, `.`, or `..`. A reserved `{+x}` value expands unescaped to keep the slashes in a resource name, which also passes `:` and `?` through. Unguarded, `versions/1:access` would reach the excluded `access` method, and `projects/p:setIamPolicy?x=` would turn a POST `getIamPolicy` into `setIamPolicy` with the caller's body. So a reserved value must match the parameter's pattern, must not be empty or start with `/`, and must hold no `?`, `#`, `%`, `\`, or dot segment. `CUSTOM_METHOD` refuses a `:` and a run of letters that ends a segment, the shape of a custom method suffix. `sha256:0a1b` passes, because its run has digits.

**Parameters.** `prepare` refuses every name in `GLOBAL_PARAMS`, such as `key`, `alt`, and `uploadType`, except `fields`. Every request carries `alt=json`, so a call never downloads media.

**Bodies.** Only a POST that declares a request schema takes a body. Every key at every depth must be a property of that schema, in camelCase or the proto's snake_case, because Google's JSON parsers accept both. `prepare` refuses a `newTransaction`, `transaction`, or `transactionOptions` key in any spelling, because Firestore and Datastore reads could otherwise open a read-write transaction.

**Responses.** `Gcp.call` redacts, then `bound` drops trailing items from the largest list until the compact JSON fits `max_response_bytes`. The executor does not follow redirects.

## Authentication guards the server, not Google

With `--require-auth` over HTTP, the server is an OAuth resource server as the MCP authorization specification describes. [`gcp_mcp/server.py`](../gcp_mcp/server.py) builds a fastmcp `RemoteAuthProvider`, which serves RFC 9728 metadata at `/.well-known/oauth-protected-resource/mcp` and answers a request without a valid token with 401 and a `WWW-Authenticate` header that points there.

`StrictJWTVerifier` checks the signature, `iss`, `aud`, and the required scopes. It requires `exp` and refuses a future `nbf` or an `iat` more than five minutes ahead. The audience defaults to the base URL plus `/mcp`, the resource that the metadata names. With `--auth-allowed-subjects`, a token passes only when its `sub` or `email` claim equals an entry.

A client token decides only whether the server answers. Calls to Google use the server's own Application Default Credentials, never the client token. Over stdio, the server runs as the operator who started it, and [`gcp_mcp/settings.py`](../gcp_mcp/settings.py) refuses `--require-auth`.

## How the tests prove it

[`tests/test_policy.py`](../tests/test_policy.py) runs `admit` over every method of all 604 documents, with oracles that do not reuse the policy's tables. Every admitted POST has a read name and returns no operation. No admitted name holds one of 83 write words, except 29 reviewed methods where the word names no write. `KNOWN_WRITES` and `KNOWN_SECRETS` stay excluded and `KNOWN_READS` stays admitted. A walk of every admitted response schema requires `REDACTED_FIELDS` to cover each credential-shaped field and to spare every cursor.

Three goldens pin the result. `policy.json` lists admitted POSTs and refused GETs, `catalog.json` lists services and default versions, and `read_methods.txt` lists the 4951 admitted methods in default versions. `scripts/golden.py` diffs them in CI, so a rule or corpus change is a reviewed diff.

[`tests/test_layering.py`](../tests/test_layering.py) keeps `policy` and `errors` on the standard library and the server modules away from the HTTP client. It walks the syntax tree to prove that `parse_service` alone constructs a `ReadMethod` and `prepare` alone constructs a `PreparedRequest`. [`tests/test_rest.py`](../tests/test_rest.py) replays both path attacks, the snake_case transaction key, and a nested password beside the cursors. [`tests/conftest.py`](../tests/conftest.py) makes every socket connect fail.

Before the policy and the executor merged, an independent reviewer mutated each one and saw the suite fail, and reproduced the goldens with separate code.

## What the server does not do

- It does not page for the model. One call returns one page and its `next_page_token`.
- It does not forward client tokens or act as the caller.
- It does not fetch discovery documents at runtime.

## Known limits

- Inference and generation endpoints are excluded. That covers `predict`, `generateContent`, Vertex AI Search, Retail search, Dialogflow knowledge search, and Document AI Warehouse search. They observe no stored state, and some store their results.
- `CUSTOM_METHOD` refuses a reserved path segment that ends in `:` and letters only. So a legacy domain-scoped project id such as `example.com:project`, or a hex digest that is all letters, cannot pass through a `{+x}` parameter.
- Redaction matches key names. A number or boolean under a credential-shaped key stays. A credential stored as a data value, such as a compute metadata `items[].value` or a label value, goes through.
- `bound` caps the response that the model gets, not the bytes that the server reads from Google.
- The allowlist matches the `email` claim without checking `email_verified`. With an issuer other than Google, list `sub` values.
- A token without a required scope gets 401, not 403, in fastmcp 4.1.6.
- GET on `/mcp` returns 405, because the transport is stateless.
- The catalog is pinned to the installed `google-api-python-client`. New services arrive through a dependency bump that regenerates the goldens.
- A request body may use camelCase or snake_case field names. `prepare` validates both against the schema.
