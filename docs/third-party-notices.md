# Third-party software and material

The repository's [MIT License](../LICENSE) applies to its original source,
documentation, skills, and examples. It does not relicense dependencies,
container images, model weights, retrieved material, or vendor trademarks.

## Database, search, and model artifacts

| Component | License boundary | Upstream source |
| --- | --- | --- |
| MongoDB Community Server | SSPL-1.0; separate from this application's MIT license | [MongoDB licensing](https://www.mongodb.com/legal/licensing/community-edition), [SSPL text](https://www.mongodb.com/legal/licensing/server-side-public-license) |
| MongoDB Community Search / `mongot` | SSPL-1.0; retains its own third-party notices | [Official mongot repository](https://github.com/mongodb/mongot), [license](https://github.com/mongodb/mongot/blob/main/LICENSE) |
| Default `sentence-transformers/all-MiniLM-L6-v2` weights | Model card identifies Apache-2.0; downloaded into the local model cache | [Publisher model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2) |

MongoDB's SSPL is source-available and has not been approved by the Open Source
Initiative. Publishing this application under MIT does not make its MongoDB and
search dependencies MIT-licensed or OSI-approved. See
[MongoDB's SSPL FAQ](https://www.mongodb.com/legal/licensing/server-side-public-license/faq)
for its distinction between an application using MongoDB and offering MongoDB's
functionality as a service.

Changing the configured embedding model changes the model license boundary.
Check the exact publisher, revision, license, and required attribution before
redistributing model weights. The source release does not include the downloaded
model cache or container images.

## Python packages and container images

Direct runtime dependencies are declared in [pyproject.toml](../pyproject.toml).
Runtime and development transitive dependencies are pinned in
[requirements.lock](../requirements.lock) and
[requirements-dev.lock](../requirements-dev.lock). Their installed distributions
retain their own license and notice files; installing them does not place them
under this project's MIT License.

The stack also uses the pinned upstream Python and MongoDB container images and
their operating-system packages. The optional introduction website uses the
upstream Nginx unprivileged Alpine image specified in
[its Compose file](../deploy/website/compose.yml). Publishing a combined binary or container image
requires retaining the license/notice material for the exact included packages.
The application image copies `mongodump` and `mongorestore` from the MongoDB image;
their upstream license and third-party notices are included in
[the release notice directory](../deploy/licenses/mongodb-database-tools/) and
preserved under `/usr/share/doc/mongodb-database-tools/` in the runtime image. This document is a
source-release inventory, not a replacement for the complete notices required by
a redistributed image.

Before distributing binaries, record the installed dependency versions and
preserve the license files shipped with their wheels/packages. Consult the official
[Database Tools source and license](https://github.com/mongodb/mongo-tools).
Do not substitute a list of package names for their required notices.

## Skills, examples, and ingested sources

Skills contain original guidance with links to supporting documentation. Those
links do not include or transfer the linked publishers' license rights.
The payment example describes fictional systems and incidents for demonstration.
It is not a record of an organization or financial production guidance.

Documents, code, web pages, and citations you ingest keep their existing ownership
and license. Retain source attribution and respect the rights that apply to
storage, excerpts, exports, and publication. Access to a source and approval
inside the platform do not grant redistribution rights.

Product and vendor names identify compatibility or dependencies; their trademark
rights remain with their respective owners.

## Console ingress and optional identity provider

The console uses the committed `dashboard/package-lock.json`,
[dependency inventory](../dashboard/DEPENDENCIES.md),
[MIT license](../dashboard/LICENSE) and
[complete frontend notices](../dashboard/THIRD_PARTY_NOTICES.txt). The static
image retains the notices and supplemental license files. Node is a pinned
build stage, not a production JavaScript server. nginx is BSD-licensed;
its official image retains its upstream component notices.

The pinned optional Keycloak image is Apache-2.0, with its own bundled JVM and
third-party notices; PostgreSQL uses the [PostgreSQL license](https://www.postgresql.org/about/licence/).
These images do not become part of this repository's MIT code license.
When redistributing container binaries, retain their full upstream notices and
meet their corresponding-source obligations, including Git (GPL-2.0) and its
Debian runtime libraries. The API image copies Debian package copyright files
and common licenses with Git/GSSAPI/CA runtime files from a pinned matching
Debian image; build tools are excluded from the final image. Use the exact
package versions in that image to obtain corresponding Debian source, rather
than assuming a moving package mirror matches a binary.
