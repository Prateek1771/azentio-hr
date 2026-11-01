-- Phase 2 hardening: a document item's status is a projection of its documents' decisions, enforced here.
-- The source of truth is doc_decisions -> documents.status; the item follows through documents.service.item_status_for.

-- Mirror of documents.service.ITEM_STATUS (test_phase2 asserts they agree).
CREATE FUNCTION doc_item_status(s text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE s WHEN 'Rejected' THEN 'Correction Requested' ELSE s END
$$;

CREATE FUNCTION items_status_from_document() RETURNS trigger AS $$
BEGIN
  IF NEW.kind <> 'document' THEN
    RETURN NEW;
  END IF;
  IF TG_OP = 'UPDATE' AND NEW.status IS NOT DISTINCT FROM OLD.status THEN
    RETURN NEW;  -- only real status changes are checked (due-date replays etc. pass)
  END IF;
  IF NEW.status IN ('Not Submitted', 'Cancelled') THEN
    RETURN NEW;  -- the initial state, and a replay cancelling a requirement
  END IF;
  IF current_setting('azentio.backfill', true) = '1' THEN
    RETURN NEW;  -- the one-time graph -> Postgres backfill copies history as it was (records.backfill sets this LOCAL)
  END IF;
  IF NEW.status NOT IN ('Submitted', 'Under Review', 'Accepted', 'Correction Requested') THEN
    RAISE EXCEPTION 'document item %: "%" is not a status a document decision can produce', NEW.id, NEW.status;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM documents d WHERE d.item_id = NEW.id AND doc_item_status(d.status) = NEW.status) THEN
    RAISE EXCEPTION 'document item % can only become "%" through a document decision (none backs it)', NEW.id, NEW.status;
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER items_status_from_document BEFORE INSERT OR UPDATE OF status ON items
  FOR EACH ROW EXECUTE FUNCTION items_status_from_document();

-- Demo records are labelled as such, never as an AI or human decision.
ALTER TABLE doc_decisions DROP CONSTRAINT doc_decisions_kind_check;
ALTER TABLE doc_decisions ADD CONSTRAINT doc_decisions_kind_check CHECK (kind IN (
  'auto_accept', 'auto_correction', 'routed_review', 'human_accept', 'human_reject', 'human_correction',
  'human_override', 'spot_check_pass', 'spot_check_fail', 'backfill', 'demo_seed'));
