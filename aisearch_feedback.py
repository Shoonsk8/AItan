import os, torch
from sentence_transformers import util as st_util
import aisearch_logic as logic

VERSION = "1.0"
BOOST_WEIGHT = 0.25   # how much feedback influences the final score
QUERY_THRESHOLD = 0.5 # min similarity to consider a past query relevant

def _path(project_name):
    return f"feedback_{project_name}.pt"

def load(project_name):
    p = _path(project_name)
    if os.path.exists(p):
        return torch.load(p)
    d = logic.EMBEDDING_DIM
    return {"query_embs": torch.empty((0, d)), "result_embs": torch.empty((0, d))}

def record(project_name, query_emb, result_emb):
    """Save a confirmed (query, result) pair."""
    data = load(project_name)
    data["query_embs"]  = torch.cat([data["query_embs"],  query_emb.unsqueeze(0).cpu()])
    data["result_embs"] = torch.cat([data["result_embs"], result_emb.unsqueeze(0).cpu()])
    torch.save(data, _path(project_name))

def boost_scores(query_emb, candidate_embs, feedback):
    """
    Returns a boost tensor (N_candidates,) in range [0, BOOST_WEIGHT].
    Zero if no relevant feedback exists yet.
    """
    if feedback["query_embs"].shape[0] == 0:
        return torch.zeros(candidate_embs.shape[0])

    # How similar is the current query to each past query?
    q_sims = st_util.cos_sim(query_emb.cpu(), feedback["query_embs"])[0]  # (N_feedback,)

    # Only use feedback from sufficiently similar past queries
    mask = q_sims >= QUERY_THRESHOLD
    if not mask.any():
        return torch.zeros(candidate_embs.shape[0])

    q_sims_masked   = q_sims[mask]                    # (K,)
    result_embs_rel = feedback["result_embs"][mask]   # (K, 512)

    # For each candidate, weighted similarity to past confirmed results
    r_sims = st_util.cos_sim(candidate_embs.cpu(), result_embs_rel)  # (M, K)
    weighted = (r_sims * q_sims_masked).sum(dim=1) / (q_sims_masked.sum() + 1e-8)  # (M,)

    return (weighted * BOOST_WEIGHT).clamp(0, BOOST_WEIGHT)
