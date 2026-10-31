"""Tasks that query NCBI run on the "ingest" queue (a single process).

The ``app.pubmed`` throttle is per process (3 req/s without key, 10 with key):
several processes on this queue would multiply the rate beyond the NCBI cap.
Compose serves this queue only to "worker-ingest" (--concurrency=1).
"""

from app.tasks import INGEST_QUEUE, complete_fulltext_task, import_pubmed_task


def test_ncbi_tasks_are_routed_to_the_ingest_queue():
    assert INGEST_QUEUE == "ingest"
    assert import_pubmed_task.queue == INGEST_QUEUE
    assert complete_fulltext_task.queue == INGEST_QUEUE
