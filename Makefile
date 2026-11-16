.PHONY: doctor test lint scan scan-loop review telegram-id

doctor:        ## check tools and keys
	uv run python -m app.doctor

test:
	uv run pytest -q

lint:
	uv run ruff check app tests

scan:          ## one pass over the Content Rewards board
	uv run python -m app.cli scan

scan-loop:     ## scan every hour
	uv run python -m app.cli scan --loop

review:        ## open the review dashboard at http://127.0.0.1:8000
	uv run uvicorn app.review.server:app --reload --port 8000

telegram-id:   ## print your chat id after you message the bot once
	uv run python -m app.cli telegram-id
