-- Phase 2: a reviewer claims a work item so two people never decide the same document at once.
ALTER TABLE work_queue ADD COLUMN claimed_at timestamptz;
