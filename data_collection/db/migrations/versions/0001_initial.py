"""Initial schema for finance pipeline."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


class Vector(sa.types.UserDefinedType):
    def __init__(self, dimensions: int) -> None:
        self.dimensions = dimensions

    def get_col_spec(self) -> str:
        return f"vector({self.dimensions})"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "universe_snapshot",
        sa.Column("asof_date", sa.Date, nullable=False),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("weight", sa.Numeric),
        sa.Column("source", sa.Text),
        sa.PrimaryKeyConstraint("asof_date", "symbol"),
    )

    op.create_table(
        "universe_member",
        sa.Column("symbol", sa.Text, primary_key=True),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("added_at", sa.Date),
        sa.Column("removed_at", sa.Date),
        sa.Column("source", sa.Text),
    )

    op.create_table(
        "issuer",
        sa.Column("cik", sa.String(length=10), primary_key=True),
        sa.Column("legal_name", sa.Text),
        sa.Column("sic", sa.Text),
        sa.Column("state", sa.Text),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "security",
        sa.Column("security_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("ticker", sa.Text),
        sa.Column("exchange", sa.Text),
        sa.Column("cik", sa.String(length=10), sa.ForeignKey("issuer.cik")),
        sa.Column("is_primary", sa.Boolean, server_default=sa.text("false")),
        sa.Column("valid_from", sa.Date),
        sa.Column("valid_to", sa.Date),
    )

    op.create_table(
        "ticker_history",
        sa.Column("cik", sa.String(length=10)),
        sa.Column("ticker", sa.Text),
        sa.Column("start_date", sa.Date),
        sa.Column("end_date", sa.Date),
        sa.Column("source", sa.Text),
        sa.Column("source_ref", sa.Text),
    )

    op.create_table(
        "corporate_action",
        sa.Column("action_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("cik", sa.String(length=10)),
        sa.Column("ticker", sa.Text),
        sa.Column("action_type", sa.Text),
        sa.Column("ex_date", sa.Date),
        sa.Column("pay_date", sa.Date),
        sa.Column("ratio", sa.Numeric),
        sa.Column("cash_amount", sa.Numeric),
        sa.Column("currency", sa.Text),
        sa.Column("source", sa.Text),
        sa.Column("source_ref", sa.Text),
    )

    op.create_table(
        "raw_object",
        sa.Column("raw_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("source", sa.Text),
        sa.Column("object_key", sa.Text),
        sa.Column("content_type", sa.Text),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("sha256", sa.Text),
        sa.Column("http_status", sa.Integer),
        sa.Column("meta", postgresql.JSONB),
    )

    op.create_table(
        "ingest_cursor",
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("cursor_key", sa.Text, nullable=False),
        sa.Column("cursor_value", sa.Text),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("source", "cursor_key"),
    )

    op.create_table(
        "market_bar_daily",
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("open", sa.Numeric),
        sa.Column("high", sa.Numeric),
        sa.Column("low", sa.Numeric),
        sa.Column("close", sa.Numeric),
        sa.Column("volume", sa.BigInteger),
        sa.Column("vwap", sa.Numeric),
        sa.Column("trade_count", sa.BigInteger),
        sa.Column("raw_id", sa.BigInteger, sa.ForeignKey("raw_object.raw_id")),
        sa.Column("source", sa.Text),
        sa.PrimaryKeyConstraint("symbol", "date"),
    )

    op.create_table(
        "market_bar_intraday",
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric),
        sa.Column("high", sa.Numeric),
        sa.Column("low", sa.Numeric),
        sa.Column("close", sa.Numeric),
        sa.Column("volume", sa.BigInteger),
        sa.Column("vwap", sa.Numeric),
        sa.Column("trade_count", sa.BigInteger),
        sa.Column("raw_id", sa.BigInteger, sa.ForeignKey("raw_object.raw_id")),
        sa.Column("source", sa.Text),
        sa.PrimaryKeyConstraint("symbol", "ts"),
    )

    op.create_table(
        "sec_filing",
        sa.Column("accession", sa.Text, primary_key=True),
        sa.Column("cik", sa.String(length=10)),
        sa.Column("form_type", sa.Text),
        sa.Column("filed_at", sa.Date),
        sa.Column("report_period", sa.Date),
        sa.Column("is_amendment", sa.Boolean),
        sa.Column("amends_accession", sa.Text),
        sa.Column("primary_doc_url", sa.Text),
        sa.Column("raw_id", sa.BigInteger, sa.ForeignKey("raw_object.raw_id")),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "sec_filing_section",
        sa.Column("accession", sa.Text, nullable=False),
        sa.Column("section_name", sa.Text, nullable=False),
        sa.Column("text_object_key", sa.Text),
        sa.Column("parser_version", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "insider_transaction",
        sa.Column("accession", sa.Text, nullable=False),
        sa.Column("cik", sa.String(length=10)),
        sa.Column("owner_name", sa.Text),
        sa.Column("transaction_date", sa.Date),
        sa.Column("code", sa.Text),
        sa.Column("shares", sa.Numeric),
        sa.Column("price", sa.Numeric),
        sa.Column("acquired_disposed", sa.Text),
        sa.Column("raw_fields", postgresql.JSONB),
    )

    op.create_table(
        "sec_13f_holding",
        sa.Column("accession", sa.Text, nullable=False),
        sa.Column("manager_cik", sa.String(length=10)),
        sa.Column("issuer_cusip", sa.Text),
        sa.Column("shares", sa.Numeric),
        sa.Column("value", sa.Numeric),
        sa.Column("put_call", sa.Text),
        sa.Column("raw_fields", postgresql.JSONB),
    )

    op.create_table(
        "document",
        sa.Column("doc_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("source", sa.Text),
        sa.Column("source_id", sa.Text),
        sa.Column("doc_type", sa.Text),
        sa.Column("cik", sa.String(length=10)),
        sa.Column("ticker", sa.Text),
        sa.Column("title", sa.Text),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("url", sa.Text),
        sa.Column("raw_id", sa.BigInteger, sa.ForeignKey("raw_object.raw_id")),
        sa.Column("text_object_key", sa.Text),
        sa.Column("text", sa.Text),
    )

    op.create_table(
        "document_chunk",
        sa.Column("chunk_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("doc_id", sa.BigInteger, sa.ForeignKey("document.doc_id")),
        sa.Column("chunk_index", sa.Integer),
        sa.Column("text", sa.Text),
        sa.Column("token_count", sa.Integer),
        sa.Column("embedding", Vector(1536)),
        sa.Column("active", sa.Boolean, server_default=sa.text("true")),
    )

    op.create_table(
        "document_fts",
        sa.Column("doc_id", sa.BigInteger, sa.ForeignKey("document.doc_id"), primary_key=True),
        sa.Column("tsv", postgresql.TSVECTOR),
    )

    op.create_table(
        "ir_feed_registry",
        sa.Column("feed_id", sa.Text, primary_key=True),
        sa.Column("cik", sa.String(length=10)),
        sa.Column("symbol", sa.Text),
        sa.Column("feed_url", sa.Text),
        sa.Column("status", sa.Text),
        sa.Column("etag", sa.Text),
        sa.Column("last_modified", sa.Text),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "news_relevance",
        sa.Column("doc_id", sa.BigInteger, sa.ForeignKey("document.doc_id"), primary_key=True),
        sa.Column("score", sa.Numeric),
        sa.Column("features_json", postgresql.JSONB),
        sa.Column("model_version", sa.Text),
        sa.Column("scored_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "macro_observation",
        sa.Column("source_system", sa.Text, nullable=False),
        sa.Column("series_id", sa.Text, nullable=False),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("value", sa.Numeric),
        sa.Column("raw_id", sa.BigInteger, sa.ForeignKey("raw_object.raw_id")),
        sa.Column("meta", postgresql.JSONB),
        sa.PrimaryKeyConstraint("source_system", "series_id", "date"),
    )

    op.create_table(
        "earnings_event",
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("earnings_date", sa.Date, nullable=False),
        sa.Column("time_of_day", sa.Text),
        sa.Column("eps_est", sa.Numeric),
        sa.Column("eps_actual", sa.Numeric),
        sa.Column("revenue_est", sa.Numeric),
        sa.Column("revenue_actual", sa.Numeric),
        sa.Column("raw_id", sa.BigInteger, sa.ForeignKey("raw_object.raw_id")),
        sa.Column("source", sa.Text),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("symbol", "earnings_date"),
    )

    op.create_index("idx_document_fts_tsv", "document_fts", ["tsv"], postgresql_using="gin")
    op.create_index("idx_document_chunk_doc", "document_chunk", ["doc_id", "chunk_index"])
    op.create_index(
        "idx_document_chunk_embedding",
        "document_chunk",
        ["embedding"],
        postgresql_using="ivfflat",
        postgresql_with={"lists": 100},
    )


def downgrade() -> None:
    op.drop_index("idx_document_chunk_embedding", table_name="document_chunk")
    op.drop_index("idx_document_chunk_doc", table_name="document_chunk")
    op.drop_index("idx_document_fts_tsv", table_name="document_fts")

    op.drop_table("earnings_event")
    op.drop_table("macro_observation")
    op.drop_table("news_relevance")
    op.drop_table("ir_feed_registry")
    op.drop_table("document_fts")
    op.drop_table("document_chunk")
    op.drop_table("document")
    op.drop_table("sec_13f_holding")
    op.drop_table("insider_transaction")
    op.drop_table("sec_filing_section")
    op.drop_table("sec_filing")
    op.drop_table("market_bar_intraday")
    op.drop_table("market_bar_daily")
    op.drop_table("ingest_cursor")
    op.drop_table("raw_object")
    op.drop_table("corporate_action")
    op.drop_table("ticker_history")
    op.drop_table("security")
    op.drop_table("issuer")
    op.drop_table("universe_member")
    op.drop_table("universe_snapshot")

    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
    op.execute("DROP EXTENSION IF EXISTS vector")
