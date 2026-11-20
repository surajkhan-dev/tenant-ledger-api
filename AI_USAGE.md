# AI Usage
- Tool: OpenAI ChatGPT.
- Prompts: design a multi-tenant ledger data model and FastAPI endpoint structure.
- Prompts: draft validation rules, SQLAlchemy models, transaction logic, and pytest cases.
- Prompts: review tenant isolation, Decimal arithmetic, bulk partial success, transfers, reversals, audit behavior, and pagination.
- The developer reviewed the proposed implementation and ran local tests during development.
- GitHub Actions ran the test suite on Python 3.12; the recorded baseline had 21 passing tests.
- Additional regression tests were added for pagination, malformed bulk rows, blank reversal references, and cross-firm reversal attempts.
- AI suggestions were treated as drafts and verified through automated tests and code review.
