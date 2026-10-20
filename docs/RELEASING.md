# Releases

Publish code and documentation from this repository. Publish datasets and models in separate Hugging Face repositories; their IDs are still to be chosen.

1. Run the tests from a clean source copy and inspect the proposed Git files for secrets and generated artifacts. Keep data/, runs/, samples/, build/ and credentials local.
2. Give each dataset release a stable version and card: original sources, applicable terms, cleaning/labels, deduplication, splits, exact counts and known limitations. Distinguish collected posts, new post IDs and new training text. Include aggregate validation reports and provenance, excluding credentials and personal machine paths.
3. Give each model release a card: base model and its terms, exact dataset revision, training settings, code commit, evaluation and limitations. Upload the final model/tokenizer rather than intermediate optimizer checkpoints.
4. Record the actual Hugging Face repository IDs and revisions in VERSIONS.md and link them from README.md after upload.

The MIT license covers project code only. Forum text, external data and base-model weights retain their own applicable terms; do not assign them the code license by default. No Hugging Face release has been created by this cleanup.

## Cleanup verification (2026-10-19)

A clean source copy with a fresh Python 3.12 environment installed from requirements-data.txt passes all 51 tests, without local datasets or checkpoints. Fresh installation exposed a missing lxml dependency and a BeautifulSoup version difference in surrogate-entity handling; extraction dependencies are now explicit and pinned to the tested versions. Dependency consistency checks pass. CUDA training dependencies record the installed development versions; a fresh GPU installation/training run was not performed for this cleanup.

The proposed source files were checked for credential patterns and local .env secret values; the only pattern match was the synthetic example.test proxy in test_transport.py. No Git history exists yet. Models, datasets, raw pages, secrets, build trees and caches are excluded. Nothing was uploaded or published.
