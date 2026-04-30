-- Adds 8-K Item codes to sec_filing so the position monitor can
-- distinguish routine filings (Item 5.07 voting results, Item 9.01
-- exhibits, etc.) from material thesis-breaking ones (Item 1.03
-- bankruptcy, Item 4.02 non-reliance, etc.) without relying on a
-- generic severity flag.

ALTER TABLE sec_filing
    ADD COLUMN IF NOT EXISTS items text[];

CREATE INDEX IF NOT EXISTS idx_sec_filing_items
    ON sec_filing USING GIN(items);
