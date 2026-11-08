import re
from collections import defaultdict
from collections.abc import Iterator, Mapping
from typing import Any

import pytest

from gcp_mcp.policy import DENY, REDACTED_FIELDS, Admitted, Excluded, admit, verb
from scripts import golden
from tests.corpus import CorpusMethod, bundled_documents, bundled_methods

GET_NOT_A_READ = "GET whose name is not a read verb; some GET methods mutate"
SECRETS = "reveals credentials or secrets"
INFERENCE = "runs model inference and stores results"

WRITE_WORDS = frozenset(
    """
    create insert delete update patch set add remove move cancel start stop restart restore
    reset resize attach detach enable disable import export generate sign undelete purge
    rollback pause resume run execute collect deploy publish send write upload apply approve
    reject abandon acknowledge ack pull push modify mutate rotate migrate promote provision
    register unregister assign unassign bind unbind claim release revoke grant suspend
    reactivate repair recreate replace switch transfer trigger invoke encrypt decrypt subscribe
    unsubscribe listen watch lock unlock begin commit finish complete verify
    """.split()
)

REVIEWED_WRITE_WORDS: Mapping[str, str] = {
    "androiddeviceprovisioning.partners.devices.getSimLockState": "'lock' is a noun",
    "androidenterprise.users.getAvailableProductSet": "'set' is a noun",
    "assuredworkloads.organizations.locations.workloads.analyzeWorkloadMove": "analyzes a move",
    "chromemanagement.customers.reports.countDevicesPerReleaseChannel": "'release' is a noun",
    "classroom.courses.announcements.getAddOnContext": "'add-on' is a noun",
    "classroom.courses.courseWork.getAddOnContext": "'add-on' is a noun",
    "classroom.courses.courseWorkMaterials.getAddOnContext": "'add-on' is a noun",
    "classroom.courses.posts.getAddOnContext": "'add-on' is a noun",
    "cloudasset.analyzeMove": "analyzes a move",
    "compute.routers.getNamedSet": "'set' is a noun",
    "config.projects.locations.deployments.exportLock": "returns the lock in the response",
    "datacatalog.projects.locations.taxonomies.export": "returns taxonomies in the response",
    "datastore.projects.runAggregationQuery": "runs a read query",
    "datastore.projects.runQuery": "runs a read query",
    "domains.projects.locations.registrations.retrieveRegisterParameters": "returns prices",
    "domains.projects.locations.registrations.retrieveTransferParameters": "returns prices",
    "drive.files.export": "returns the file in the response",
    "firestore.projects.databases.documents.runAggregationQuery": "runs a read query",
    "firestore.projects.databases.documents.runQuery": "runs a read query",
    "health.users.dataTypes.dataPoints.exportExerciseTcx": "returns data in the response",
    "playdeveloperreporting.apps.fetchReleaseFilterOptions": "'release' is a noun",
    "run.projects.locations.exportImageMetadata": "returns metadata in the response",
    "run.projects.locations.exportMetadata": "returns metadata in the response",
    "run.projects.locations.exportProjectMetadata": "returns metadata in the response",
    "run.projects.locations.jobs.executions.exportStatus": "returns status in the response",
    "run.projects.locations.services.revisions.exportStatus": "returns status in the response",
    "vmwareengine.projects.locations.getDnsBindPermission": "'bind' is a noun",
    "workflowexecutions.projects.locations.workflows.executions.exportData": "returns data",
}

POST_READ_NAME = re.compile(
    r"(get|list|search|query|lookup|fetch|aggregate|count|check)(?![a-z])"
    r"|(runQuery|runAggregationQuery|batchGet|troubleshoot|testIamPermissions)$"
)

CREDENTIAL_NOUNS = frozenset(
    {"secret", "password", "passwords", "passwd", "passphrase", "credential", "jwt", "psk", "pin"}
    | {"cak", "hmac", "otp"}
)
KEY_KINDS = frozenset(
    """
    private api client consumer signer shared webhook secret access account activation pairing
    hmac raw master license
    """.split()
)
TOKEN_KINDS = frozenset(
    """
    access refresh auth authentication authorization bearer id oauth security session sas
    activation enrollment migration registration api debug verification
    """.split()
)
WRAPPER_WORDS = frozenset({"data", "file", "value", "pem", "text"})
OUTPUT_ONLY_SECRET = re.compile(
    r"(?i)\boutput only\.\s+(?:the\s+)?(?:(?!next|primary)[\w-]+\s+){0,3}"
    r"(?:secret|password|token|key)(?:\s+value|\s+string|\s+in base64)?\."
)
CURSOR = re.compile(r"(?i)page_?token|next page|pagination")

KNOWN_RESPONSE_SECRETS: Mapping[str, Mapping[str, str]] = {
    "androidmanagement.enterprises.devices.get": {
        "enrollmentTokenData": "data an admin attached to the token, not the token",
    },
    "androidmanagement.enterprises.devices.list": {
        "devices.enrollmentTokenData": "data an admin attached to the token, not the token",
    },
}

KNOWN_WRITES = (
    "compute.instances.insert",
    "compute.instances.delete",
    "aiplatform.projects.locations.datasets.datasetVersions.restore",
    "cloudbilling.organizations.billingAccounts.move",
    "beyondcorp.organizations.locations.subscriptions.cancel",
    "beyondcorp.organizations.locations.subscriptions.restart",
    "datalabeling.projects.operations.cancel",
    "cloudbilling.tasks.subscribe",
    "discoveryengine.projects.locations.userEvents.collect",
    "developerconnect.projects.locations.accountConnectors.users.startOAuthFlow",
    "healthcare.projects.locations.datasets.fhirStores.fhir.bulk-export",
    "bigquery.jobs.query",
    "pubsub.projects.subscriptions.pull",
    "cloudasset.exportAssets",
    "cloudasset.queryAssets",
    "spanner.projects.instances.databases.sessions.executeSql",
    "firestore.projects.databases.documents.listen",
    "aiplatform.projects.locations.reasoningEngines.query",
    "aiplatform.projects.locations.extensions.query",
    "genomics.projects.workers.checkIn",
    "merchantapi.accounts.dataSources.fetch",
    "ml.projects.locations.studies.trials.checkEarlyStoppingState",
    "notebooks.projects.locations.instances.checkAuthorization",
    "discoveryengine.projects.locations.groundingConfigs.check",
    "discoveryengine.projects.locations.dataStores.servingConfigs.search",
    "discoveryengine.projects.locations.collections.engines.servingConfigs.searchLite",
    "retail.projects.locations.catalogs.servingConfigs.search",
    "retail.projects.locations.catalogs.placements.search",
    "dialogflow.projects.locations.suggestions.searchKnowledge",
)

KNOWN_SECRETS = (
    "secretmanager.projects.secrets.versions.access",
    "secretmanager.projects.locations.secrets.versions.access",
    "parametermanager.projects.locations.parameters.versions.render",
    "redis.projects.locations.instances.getAuthString",
    "apikeys.projects.locations.keys.getKeyString",
    "vmwareengine.projects.locations.privateClouds.showNsxCredentials",
    "iamcredentials.projects.serviceAccounts.generateAccessToken",
    "iam.projects.locations.oauthClients.credentials.get",
    "iap.projects.brands.identityAwareProxyClients.list",
    "apigee.organizations.developers.apps.keys.get",
    "identitytoolkit.projects.accounts.batchGet",
    "identitytoolkit.projects.accounts.lookup",
    "cloudkms.projects.locations.keyRings.cryptoKeys.cryptoKeyVersions.exportTrustedKeyWrappedCryptoKeyVersion",
    "monitoring.projects.notificationChannels.getVerificationCode",
    "integrations.projects.locations.authConfigs.get",
    "integrations.projects.locations.authConfigs.list",
    "integrations.projects.locations.products.authConfigs.get",
    "integrations.projects.locations.products.authConfigs.list",
    "redis.projects.locations.clusters.tokenAuthUsers.authTokens.get",
    "redis.projects.locations.clusters.tokenAuthUsers.authTokens.list",
    "androidmanagement.enterprises.enrollmentTokens.get",
    "androidmanagement.enterprises.enrollmentTokens.list",
    "androidmanagement.enterprises.migrationTokens.get",
    "androidmanagement.enterprises.migrationTokens.list",
    "firebaseappdistribution.apps.getJwt",
    "analyticsadmin.properties.dataStreams.measurementProtocolSecrets.get",
    "analyticsadmin.properties.dataStreams.measurementProtocolSecrets.list",
    "networkconnectivity.projects.locations.serviceConnectionTokens.get",
    "networkconnectivity.projects.locations.serviceConnectionTokens.list",
    "vmwareengine.projects.locations.privateClouds.hcxActivationKeys.get",
    "vmwareengine.projects.locations.privateClouds.hcxActivationKeys.list",
    "compute.interconnects.getMacsecConfig",
    "apigee.organizations.keyvaluemaps.entries.get",
    "apigee.organizations.environments.keyvaluemaps.entries.list",
    "apigee.organizations.apis.keyvaluemaps.entries.get",
    "recommendationengine.projects.locations.catalogs.eventStores.predictionApiKeyRegistrations.list",
)

KNOWN_READS = (
    "compute.instances.get",
    "compute.instances.aggregatedList",
    "compute.zoneOperations.get",
    "compute.firewalls.list",
    "compute.backendServices.getHealth",
    "storage.buckets.get",
    "storage.buckets.getIamPolicy",
    "cloudresourcemanager.projects.getIamPolicy",
    "iam.projects.serviceAccounts.list",
    "logging.entries.list",
    "monitoring.projects.timeSeries.query",
    "cloudasset.searchAllResources",
    "cloudasset.analyzeIamPolicy",
    "container.projects.locations.clusters.list",
    "firestore.projects.databases.documents.runQuery",
    "datastore.projects.runQuery",
    "secretmanager.projects.secrets.list",
)


@pytest.mark.parametrize(
    ("args", "verdict"),
    [
        pytest.param(
            ("compute.instances.delete", "DELETE", False, "Operation", "compute.instances"),
            Excluded("mutating HTTP method DELETE"),
            id="DELETE mutates",
        ),
        pytest.param(
            ("compute.instances.update", "PATCH", True, "Instance", "compute.instances"),
            Excluded("mutating HTTP method PATCH"),
            id="PATCH mutates",
        ),
        pytest.param(
            ("compute.instances.get", "GET", False, "Instance", "compute.instances"),
            Admitted(accepts_body=False),
            id="GET get reads",
        ),
        pytest.param(
            ("compute.zoneOperations.get", "GET", False, "Operation", "compute.zoneOperations"),
            Admitted(accepts_body=False),
            id="GET on an operations resource polls",
        ),
        pytest.param(
            (
                "aiplatform.projects.locations.datasets.datasetVersions.restore",
                "GET",
                False,
                "GoogleLongrunningOperation",
                "aiplatform.projects.locations.datasets.datasetVersions",
            ),
            Excluded("starts a long-running operation"),
            id="GET that starts an operation",
        ),
        pytest.param(
            ("compute.zoneOperations.wait", "POST", False, "Operation", "compute.zoneOperations"),
            Excluded("starts a long-running operation"),
            id="POST on an operations resource does not poll",
        ),
        pytest.param(
            (
                "cloudbilling.organizations.billingAccounts.move",
                "GET",
                False,
                "BillingAccount",
                "cloudbilling.organizations.billingAccounts",
            ),
            Excluded(GET_NOT_A_READ),
            id="GET without a read verb",
        ),
        pytest.param(
            ("bigquery.jobs.query", "POST", True, "QueryResponse", "bigquery.jobs"),
            Excluded(DENY["bigquery.jobs.query"]),
            id="DENY",
        ),
        pytest.param(
            (
                "discoveryengine.projects.locations.dataStores.servingConfigs.search",
                "POST",
                True,
                "GoogleCloudDiscoveryengineV1SearchResponse",
                "discoveryengine.projects.locations.dataStores.servingConfigs",
            ),
            Excluded(INFERENCE),
            id="DENY_PATTERNS",
        ),
        pytest.param(
            ("logging.entries.list", "POST", True, "ListLogEntriesResponse", "logging.entries"),
            Admitted(accepts_body=True),
            id="POST list reads with a body",
        ),
        pytest.param(
            ("example.things.getIamPolicy", "POST", False, "Policy", "example.things"),
            Admitted(accepts_body=False),
            id="POST read without a request schema takes no body",
        ),
        pytest.param(
            (
                "pubsub.projects.subscriptions.pull",
                "POST",
                True,
                "PullResponse",
                "pubsub.projects.subscriptions",
            ),
            Excluded("POST without a read verb"),
            id="POST without a read verb",
        ),
        pytest.param(
            (
                "secretmanager.projects.secrets.versions.access",
                "GET",
                False,
                "AccessSecretVersionResponse",
                "secretmanager.projects.secrets.versions",
            ),
            Excluded(SECRETS),
            id="SENSITIVE_IDS",
        ),
        pytest.param(
            (
                "redis.projects.locations.instances.getAuthString",
                "GET",
                False,
                "InstanceAuthString",
                "redis.projects.locations.instances",
            ),
            Excluded(SECRETS),
            id="SENSITIVE name",
        ),
    ],
)
def test_admit(args: tuple, verdict: Admitted | Excluded) -> None:
    assert admit(*args) == verdict


@pytest.mark.parametrize(
    "args",
    [
        ("", "", False, None, ""),
        ("example.things.get", "HEAD", False, None, "example.things"),
        ("example.things.get", "get", False, "Thing", "example.things"),
        ("example", "GET", True, "", ""),
    ],
)
def test_admit_refuses_what_it_does_not_recognize(args: tuple) -> None:
    assert isinstance(admit(*args), Excluded)


@pytest.mark.parametrize(
    ("method_id", "expected"),
    [
        ("compute.instances.aggregatedList", "aggregated"),
        ("firestore.projects.databases.documents.listen", "listen"),
        ("cloudresourcemanager.projects.getIamPolicy", "get"),
        ("healthcare.projects.locations.datasets.fhirStores.fhir.search-type", "search"),
        ("monitoring.projects.location.prometheus.api.v1.query_range", "query"),
        ("sql.Backups.GetBackup", "get"),
        ("", ""),
    ],
)
def test_verb(method_id: str, expected: str) -> None:
    assert verb(method_id) == expected


@pytest.mark.parametrize(
    "field",
    [
        "password",
        "passwd",
        "rootPassword",
        "secret",
        "clientSecret",
        "consumerSecret",
        "sharedSecret",
        "privateKey",
        "clientKey",
        "credential",
        "decryptedCredential",
        "authString",
        "keyString",
        "accessToken",
        "refreshToken",
        "authToken",
        "bearerToken",
        "token",
        "pin",
        "passphrase",
        "secretValue",
        "privateKeyData",
        "serviceAccountKeyFile",
        "apiKey",
        "apiKeys",
        "consumerKey",
        "secretAccessKey",
        "generatedActivationKey",
        "pairingKey",
        "rawKey",
        "signerKey",
        "jwt",
        "sasToken",
        "signinEnrollmentToken",
        "thirdPartyAuthenticationTokens",
    ],
)
def test_redacted_fields_match_credential_values(field: str) -> None:
    assert REDACTED_FIELDS.search(field)


@pytest.mark.parametrize(
    "field",
    [
        "pageToken",
        "nextPageToken",
        "nextSyncToken",
        "etag",
        "fingerprint",
        "secretVersion",
        "passwordSecretVersion",
        "clientSecretSha256",
        "publicKey",
        "kmsKeyName",
        "credentialType",
        "tokens",
        "apiKeyId",
        "preSharedKeys",
        "secrets",
    ],
)
def test_redacted_fields_spare_cursors_references_and_hashes(field: str) -> None:
    assert not REDACTED_FIELDS.search(field)


def last_segment(method: CorpusMethod) -> str:
    return method.id.rpartition(".")[2]


def words(name: str) -> list[str]:
    return [word.lower() for word in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", name)]


def names_credential(field: str) -> bool:
    found = words(field)
    while len(found) > 1 and found[-1] in WRAPPER_WORDS:
        found.pop()
    if not found:
        return False
    head, kind = found[-1], found[-2] if len(found) > 1 else ""
    if head in ("key", "keys"):
        return kind in KEY_KINDS
    if head in ("token", "tokens"):
        return kind in TOKEN_KINDS or found == ["token"]
    if head == "string":
        return kind in ("auth", "key")
    return head in CREDENTIAL_NOUNS


def holds_text(prop: Mapping[str, Any]) -> bool:
    if "enum" in prop:
        return False
    if prop.get("type") == "string":
        return prop.get("format") in (None, "byte")
    if prop.get("type") == "array":
        return holds_text(prop.get("items", {}))
    extra = prop.get("additionalProperties")
    return isinstance(extra, dict) and holds_text(extra)


def ref_of(prop: Mapping[str, Any]) -> str | None:
    for candidate in (prop, prop.get("items", {}), prop.get("additionalProperties")):
        if isinstance(candidate, dict) and "$ref" in candidate:
            return candidate["$ref"]
    return None


def fields_of(
    schemas: Mapping[str, Any], name: str, depth: int, path: str = "", seen: frozenset = frozenset()
) -> Iterator[tuple[str, Mapping[str, Any]]]:
    if name in seen:
        return
    for field, prop in schemas.get(name, {}).get("properties", {}).items():
        here = f"{path}.{field}" if path else field
        yield here, prop
        ref = ref_of(prop)
        if ref and depth > 1:
            yield from fields_of(schemas, ref, depth - 1, here, seen | {name})


def admitted_text_fields() -> Iterator[tuple[str, str, Mapping[str, Any]]]:
    for schemas, methods in bundled_documents():
        for method in methods:
            if method.response_schema and isinstance(admit(*method), Admitted):
                for path, prop in fields_of(schemas, method.response_schema, depth=3):
                    if holds_text(prop):
                        yield method.id, path, prop


@pytest.fixture(scope="module")
def admitted() -> list[CorpusMethod]:
    return [method for method in bundled_methods() if isinstance(admit(*method), Admitted)]


@pytest.fixture(scope="module")
def verdict_types_by_id() -> dict[str, set[type]]:
    by_id: dict[str, set[type]] = defaultdict(set)
    for method in bundled_methods():
        by_id[method.id].add(type(admit(*method)))
    return dict(by_id)


def test_admitted_methods_use_only_get_and_post(admitted: list[CorpusMethod]) -> None:
    assert {method.http_method for method in admitted} == {"GET", "POST"}


def test_admitted_posts_have_a_read_name_and_start_no_operation(
    admitted: list[CorpusMethod],
) -> None:
    posts = [method for method in admitted if method.http_method == "POST"]
    assert posts
    assert [
        method.id
        for method in posts
        if not POST_READ_NAME.match(last_segment(method))
        or (method.response_schema or "").endswith("Operation")
    ] == []


def test_admitted_operations_are_polls(admitted: list[CorpusMethod]) -> None:
    operations = [m for m in admitted if (m.response_schema or "").endswith("Operation")]
    assert operations
    assert [
        method.id
        for method in operations
        if method.http_method != "GET"
        or not method.resource.rpartition(".")[2].lower().endswith("operations")
    ] == []


def test_admitted_names_carry_no_write_word_beyond_the_reviewed_ones(
    admitted: list[CorpusMethod],
) -> None:
    flagged = {
        method.id for method in admitted if WRITE_WORDS.intersection(words(last_segment(method)))
    }
    assert sorted(flagged) == sorted(REVIEWED_WRITE_WORDS)


def test_admitted_responses_redact_every_credential_field() -> None:
    unredacted = {
        (method_id, path)
        for method_id, path, prop in admitted_text_fields()
        if (
            names_credential(path.rpartition(".")[2])
            or OUTPUT_ONLY_SECRET.search(prop.get("description", ""))
        )
        and not any(REDACTED_FIELDS.search(field) for field in path.split("."))
    }
    reviewed = {
        (method_id, path) for method_id, paths in KNOWN_RESPONSE_SECRETS.items() for path in paths
    }
    assert sorted(unredacted) == sorted(reviewed)


def test_redaction_spares_every_pagination_cursor() -> None:
    cursors = {
        path.rpartition(".")[2]
        for _, path, prop in admitted_text_fields()
        if CURSOR.search(f"{path.rpartition('.')[2]} {prop.get('description', '')}")
    }
    assert "nextPageToken" in cursors
    assert sorted(field for field in cursors if REDACTED_FIELDS.search(field)) == []


@pytest.mark.parametrize("method_id", KNOWN_WRITES + KNOWN_SECRETS)
def test_known_writes_and_secrets_are_excluded(
    verdict_types_by_id: dict[str, set[type]], method_id: str
) -> None:
    assert verdict_types_by_id.get(method_id) == {Excluded}


@pytest.mark.parametrize("method_id", KNOWN_READS)
def test_known_reads_are_admitted(
    verdict_types_by_id: dict[str, set[type]], method_id: str
) -> None:
    assert verdict_types_by_id.get(method_id) == {Admitted}


def test_golden_file_pins_admitted_posts_and_refused_gets() -> None:
    assert golden.main(["--check"]) == 0
