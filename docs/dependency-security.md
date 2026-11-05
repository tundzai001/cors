# Dependency security

The backend and receiver agent use separate Python 3.12 dependency environments:

- `cors_dashboard/backend/requirements.txt`
- `geodetic/requirements.txt`

`.github/workflows/security.yml` resolves and audits both complete dependency
graphs on pushes to main, pull requests and manual runs. Any known vulnerability
or resolution failure fails its audit job. The SonarCloud scan runs after both
audits pass and resolves transitive Python dependencies with the same runtime.
Action versions are pinned to immutable revisions.

## SonarCloud setup

1. Store an analysis token in the repository Actions secret `SONAR_TOKEN`.
2. In SonarCloud project `tundzai001_cors`, open **Administration > Analysis Method**
   and disable **Automatic Analysis** before using CI-based analysis.
3. Run **Dependency security and SonarCloud** in GitHub Actions.

Fork pull requests can run the dependency audit, but do not receive the SonarCloud
secret. A missing token fails the scan with an explicit setup message.

## Updating dependencies

Update the pinned versions, resolve both complete dependency graphs, run
`pip-audit --strict -r <requirements file>` in Python 3.12, then run the affected
backend/agent tests before pushing. Do not suppress advisories to obtain a clean
scan. Remove unused packages only after checking source imports.

Backend JWT signing uses PyJWT with the existing explicit HS256 algorithm and
secret. Compatibility tests verify existing python-jose session tokens, session
claims, expiry and rejection of incorrect keys or algorithms. The migration
requires no token or session data rewrite. Unused desktop UI dependencies and the
unused asyncio backport are no longer installed with the services.

## Redis server advisory mapped to the Python client

SonarCloud may attach CVE-2021-31294 to the PyPI dependency `redis==5.2.1`.
This package is redis-py, the Python client (`import redis.asyncio`). It does
not contain the Redis server or its primary/replica replication implementation.
The CVE describes a Redis **server** assertion triggered by a replica sending a
SET command, fixed in the server source in 2021. It is not a redis-py advisory.

For this exact PyPI finding, record a **Safe** review with that component mismatch
and the upstream reference: https://www.cve.org/CVERecord?id=CVE-2021-31294.
This review does not verify or change the Redis server deployed separately; its
version and server advisories must be assessed independently. Do not disable the
rule or broadly exclude Redis from dependency scanning.
