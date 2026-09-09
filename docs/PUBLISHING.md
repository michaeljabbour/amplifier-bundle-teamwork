# Publication checklist

The canonical source is this repository. The Teamwork application keeps a generated compatibility adapter snapshot and download; its packaging script checks that snapshot against this source. Do not edit the generated hook copy independently.

Before publishing: run tests and bundle-load validation, resolve expert-review blockers, inspect every staged path for credentials/private data, build/check the module wheel, and run a bounded isolated live session. A local PASS does not establish other participants' installations. Publish only to the agreed owner and visibility; never infer a Microsoft organization target.

After publication: verify the actual repository URL/visibility and commit, test the remote behavior source, and link the real repository in Teamwork. Keep the existing adapter download working. No default/active bundle migration is part of publication.
