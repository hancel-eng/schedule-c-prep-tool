# Bank profiles

Each `.json` file here describes the section-header vocabulary one bank's
statements use (e.g. what phrase introduces the deposits section, the
withdrawals section, the cleared-checks listing, the daily-balance table).
It contains no client data -- only the wording a bank's own statement
template prints, the same way a bank's name is public information.

## Where these come from

`core/pdf_parser.py` ships with three banks' vocabulary hardcoded (Regions,
Capital One, Chase), because that's what real client statements have
actually shown so far. A statement from a bank outside that list won't be
recognized by the hardcoded rules.

When that happens, the app automatically:
1. Tries every profile already saved in this directory (free, instant).
2. If none match, falls back to an AI extraction for that one file (a few
   cents, only used when nothing else worked -- see `core/llm_extractor.py`).
3. If that AI extraction reconciles against the statement's own declared
   totals, asks the same model to identify the new bank's header vocabulary
   and writes a candidate profile here (`core/bank_learner.py`).
4. Re-runs the ordinary rule-based parser with that candidate profile
   against the same document. Only if it reproduces the AI-verified numbers
   exactly does the profile get kept -- otherwise it's discarded, and that
   bank keeps using the AI fallback until a profile that passes exists.

So a new bank costs a few cents the first time or two, and is free from
then on. See [README.md](../README.md) and
[ARCHITECTURE.md](../ARCHITECTURE.md) for the full pipeline.

## Reviewing a learned profile

A saved profile is worth a quick glance the first time a new bank appears
-- open the `.json` file and check the header phrases look like real
statement wording, not something oddly specific to one document. Nothing
here is committed automatically to future runs' trust without already
having passed the self-verification step above.
