# Everything here is stdlib-only: no install step, no virtualenv, no credentials.
PYTHON ?= python3
VILLA  ?= ../villa
COMMIT ?= 7d6a82c71ed05ac1bd5c2e956d15400b564583d5
OUT    ?= audit

.PHONY: test audit collect analyze crosscheck clean

test:                       ## 47 unit + CLI tests, no network
	$(PYTHON) -m unittest discover -s tests

audit:                      ## the whole audit, end to end (network, ~4 min)
	$(PYTHON) -m vcaudit all --outdir $(OUT) --workers 24 \
	    --villa-commit $(COMMIT) --villa-src $(VILLA)

collect:                    ## read the bucket only (network)
	$(PYTHON) -m vcaudit collect --out $(OUT)/raw/inventory.json \
	    --catalogue-out $(OUT)/raw/catalogue.json --workers 24

analyze:                    ## re-derive every number from the collected documents (offline)
	$(PYTHON) -m vcaudit analyze --inventory $(OUT)/raw/inventory.json \
	    --catalogue $(OUT)/raw/catalogue.json --outdir $(OUT) \
	    --villa-src $(VILLA) --villa-commit $(COMMIT)

crosscheck:                 ## pair-by-pair agreement with the 2026-09-07 measurement
	$(PYTHON) scripts/crosscheck_2026_09_07.py \
	    --theirs $(THEIRS)/audit/pairs.json \
	    --theirs-inventory $(THEIRS)/audit/inventory.json \
	    --mine $(OUT)/csv/1727_pairs.csv \
	    --mine-volumes $(OUT)/csv/1727_volumes.csv

clean:
	rm -rf $(OUT)/raw $(OUT)/csv $(OUT)/SUMMARY.md $(OUT)/summary.json
	find . -name __pycache__ -type d -exec rm -rf {} +
