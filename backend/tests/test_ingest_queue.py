"""Les tâches qui interrogent NCBI tournent sur la file « ingest » (un seul process).

Le throttle de ``app.pubmed`` est par process (3 req/s sans clé, 10 avec la clé) :
plusieurs process sur cette file multiplieraient le débit au-delà du plafond NCBI.
Compose ne sert cette file qu'à « worker-ingest » (--concurrency=1).
"""

from app.tasks import INGEST_QUEUE, complete_fulltext_task, import_pubmed_task


def test_ncbi_tasks_are_routed_to_the_ingest_queue():
    assert INGEST_QUEUE == "ingest"
    assert import_pubmed_task.queue == INGEST_QUEUE
    assert complete_fulltext_task.queue == INGEST_QUEUE
