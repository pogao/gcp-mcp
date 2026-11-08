"""Decide whether this server may ever call a Google API method.

`admit` is the only judge of HTTP methods and method names. First match wins:

1. DELETE, PATCH, and PUT mutate.
2. DENY and DENY_PATTERNS list methods that look like reads but are not.
3. SENSITIVE and SENSITIVE_IDS match reads that return credentials or secrets.
4. A long-running operation response starts work, unless the method is a GET on an
   `*operations` resource, which polls an existing operation.
5. A GET needs a leading verb in READ_VERBS or a name in READ_IDIOMS. This is default
   deny, because Google ships GET methods that mutate (`restore`, `move`, `cancel`,
   `restart`, `subscribe`).
6. A POST needs a name in READ_NAMES or a leading verb in POST_READ_VERBS. It accepts a
   body only when it declares a request schema.

Inference endpoints (predict, generateContent, translateText) are not reads. They observe
no stored state and cost money. DENY_PATTERNS also refuses searches whose request body can
ask a model to summarize and the service to keep the results, because `admit` never sees
the body.

tests/test_policy.py runs `admit` offline over every method of every discovery document
bundled with google-api-python-client, checks invariants with oracles independent of
these tables, and diffs admitted POSTs and refused GETs against tests/golden/policy.json,
so a rule or corpus change is a reviewed diff.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass

DENY: Mapping[str, str] = {
    "bigquery.jobs.query": "runs arbitrary SQL, including DML and DDL",
    "identitytoolkit.relyingparty.getOobConfirmationCode": "sends an out-of-band email",
    "firestore.projects.databases.documents.listen": "opens a server-push stream",
    "cloudasset.queryAssets": "starts a query job that can write to a BigQuery table",
    "aiplatform.projects.locations.extensions.query": "runs an extension that calls external APIs",
    "aiplatform.projects.locations.reasoningEngines.query": "runs an agent",
    "aiplatform.projects.locations.reasoningEngines.runtimeRevisions.query": "runs an agent",
    "aiplatform.reasoningEngines.query": "runs an agent",
    "aiplatform.reasoningEngines.runtimeRevisions.query": "runs an agent",
    "discoveryengine.projects.locations.groundingConfigs.check": "runs model inference",
    "genomics.projects.workers.checkIn": "reports worker status and takes work",
    "genomics.workers.checkIn": "reports worker status and takes work",
    "merchantapi.accounts.dataSources.fetch": "starts a data source fetch",
    "notebooks.projects.locations.instances.checkAuthorization": "is a step of an OAuth flow",
}

DENY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^discoveryengine\..*\.servingConfigs\.(search|searchLite)$"),
    re.compile(r"^retail\..*\.(servingConfigs|placements)\.search$"),
    re.compile(r"^dialogflow\..*\.suggestions\.searchKnowledge$"),
)

SENSITIVE = re.compile(
    r"(?i)(password|credential|secret|token|signed|downloadurl|authinfo|authstring|keystring"
    r"|privatekey|authorizationcode|verificationcode)"
)

SENSITIVE_IDS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^secretmanager\..*\.versions\.access$"),
    re.compile(r"^iam\..*\.oauthClients\.credentials\.(get|list)$"),
    re.compile(r"^iap\..*\.identityAwareProxyClients\.(get|list)$"),
    re.compile(r"^apigee\..*\.apps\.keys\.get$"),
    # Account reads return each user's password hash and salt.
    re.compile(
        r"^identitytoolkit\..*(accounts\.(batchGet|lookup|query)|queryAccounts|getAccountInfo)$"
    ),
    re.compile(r"^cloudkms\..*\.exportTrustedKeyWrappedCryptoKeyVersion$"),
)

LRO_RESPONSES = frozenset(
    {
        "Operation",
        "GoogleLongrunningOperation",
        "GoogleLongrunning_Operation",
        "GoogleLongrunning__Operation",
        "ApigatewayOperation",
        "SasPortalOperation",
    }
)

READ_VERBS = frozenset(
    {
        "get",
        "list",
        "search",
        "lookup",
        "query",
        "fetch",
        "analyze",
        "summarize",
        "calculate",
        "check",
        "find",
        "resolve",
        "show",
        "read",
        "retrieve",
        "count",
        "describe",
        "export",
        "validate",
        "compute",
        "aggregate",
        "enumerate",
        "explain",
        "is",
    }
)

READ_IDIOMS = frozenset(
    {
        "aggregated",
        "aggregatedList",
        "batchGet",
        "batchGetAssetsHistory",
        "batchGetDocumentsMetadata",
        "batchGetStats",
        "batchRead",
        "testIamPermissions",
        "Binary-read",
        "Binary-vread",
        "ConceptMap-search-translate",
        "ConceptMap-translate",
        "Consent-enforcement-status",
        "Encounter-everything",
        "Observation-lastn",
        "Patient-consent-enforcement-status",
        "Patient-everything",
        "Resource-incoming-references",
        "capabilities",
        "history",
        "vread",
        "values",
        "accessibilityClusters",
        "configuredInsight",
    }
)

READ_NAMES = frozenset(
    {
        "getIamPolicy",
        "testIamPermissions",
        "runQuery",
        "runAggregationQuery",
        "batchGet",
        "troubleshoot",
    }
)

POST_READ_VERBS = frozenset(
    {"get", "list", "search", "query", "lookup", "fetch", "aggregate", "count", "check"}
)

_LEADING_WORD = re.compile(r"[A-Za-z][a-z]*")


@dataclass(frozen=True, slots=True)
class Admitted:
    accepts_body: bool


@dataclass(frozen=True, slots=True)
class Excluded:
    reason: str


Verdict = Admitted | Excluded


def verb(method_id: str) -> str:
    word = _LEADING_WORD.match(method_id.rpartition(".")[2])
    return word[0].lower() if word else ""


def admit(
    method_id: str,
    http_method: str,
    has_request_body: bool,
    response_schema: str | None,
    resource: str,
) -> Verdict:
    """`resource` is the method id without its last segment, not a resource name."""
    if http_method in ("DELETE", "PATCH", "PUT"):
        return Excluded(f"mutating HTTP method {http_method}")
    if method_id in DENY:
        return Excluded(DENY[method_id])
    if any(pattern.match(method_id) for pattern in DENY_PATTERNS):
        return Excluded("runs model inference and stores results")
    name = method_id.rpartition(".")[2]
    if SENSITIVE.search(name) or any(pattern.match(method_id) for pattern in SENSITIVE_IDS):
        return Excluded("reveals credentials or secrets")
    owner = resource.rpartition(".")[2]
    polls_operation = http_method == "GET" and owner.lower().endswith("operations")
    if response_schema in LRO_RESPONSES and not polls_operation:
        return Excluded("starts a long-running operation")
    if http_method == "GET":
        if verb(method_id) in READ_VERBS or name in READ_IDIOMS:
            return Admitted(accepts_body=False)
        return Excluded("GET whose name is not a read verb; some GET methods mutate")
    if http_method != "POST":
        return Excluded(f"unsupported HTTP method {http_method}")
    if name in READ_NAMES or verb(method_id) in POST_READ_VERBS:
        return Admitted(accepts_body=has_request_body)
    return Excluded("POST without a read verb")
