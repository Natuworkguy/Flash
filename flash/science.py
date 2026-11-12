"""Science: the research databases Flash reads from, in any chat.

Papers from PubMed, Europe PMC (which also holds bioRxiv and medRxiv
preprints) and arXiv, with their abstracts and, when the paper is open
access, its full text; related papers; clinical trials from
ClinicalTrials.gov; compounds from PubChem with their activity in ChEMBL;
proteins and genes from UniProt; and a citation for any DOI. All of them
are free and need no key, and every answer carries links to its sources.

Each function returns plain text for the model to read. A failure is an
answer too ("Error: ..."), never an exception, so a chat goes on.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET  # nosec B405 -- answers from the databases named here, not user files
from typing import Any, Optional

TIMEOUT = 25
AGENT = "Flash/1 (science tool)"
ABSTRACT_CHARS = 1600
FULL_TEXT_CHARS = 24000
MOST = 25

PUBMED = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
EUROPE_PMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/"
ARXIV = "https://export.arxiv.org/api/query"
TRIALS = "https://clinicaltrials.gov/api/v2/studies"
PUBCHEM = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/"
CHEMBL = "https://www.ebi.ac.uk/chembl/api/data/"
UNIPROT = "https://rest.uniprot.org/uniprotkb/"


class ScienceError(Exception):
    """Said to the model as it is."""


# ----- asking -----------------------------------------------------------

def _get(url: str, params: Optional[dict] = None, accept: str = "application/json") -> bytes:
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(url, headers={"User-Agent": AGENT, "Accept": accept})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # nosec B310 -- fixed https hosts above
            return r.read(8_000_000)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ScienceError("not found") from None
        raise ScienceError(f"{urllib.parse.urlsplit(url).netloc} answered {exc.code} {exc.reason}") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ScienceError(f"could not reach {urllib.parse.urlsplit(url).netloc}: {getattr(exc, 'reason', exc)}") from None


def _json(url: str, params: Optional[dict] = None) -> Any:
    try:
        return json.loads(_get(url, params))
    except ValueError:
        raise ScienceError(f"{urllib.parse.urlsplit(url).netloc} sent something that is not JSON") from None


def _xml(url: str, params: Optional[dict] = None, accept: str = "application/xml") -> ET.Element:
    try:
        return ET.fromstring(_get(url, params, accept))  # nosec B314 -- see the import
    except ET.ParseError:
        raise ScienceError(f"{urllib.parse.urlsplit(url).netloc} sent something unreadable") from None


def _clip(text: str, n: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + " ..."


def _text(el: Optional[ET.Element]) -> str:
    return "".join(el.itertext()).strip() if el is not None else ""


def _authors(names: list[str]) -> str:
    names = [n for n in names if n]
    if not names:
        return ""
    return ", ".join(names[:3]) + (" et al." if len(names) > 3 else "")


def _limit(n: Any, default: int = 8) -> int:
    try:
        return max(1, min(MOST, int(n)))
    except (TypeError, ValueError):
        return default


# ----- what an id is ----------------------------------------------------

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+)", re.I)
PMID_RE = re.compile(r"^(?:pmid:?\s*)?(\d{4,9})$", re.I)
PMC_RE = re.compile(r"^(?:pmcid:?\s*)?(PMC\d+)$", re.I)
ARXIV_RE = re.compile(r"(?:arxiv:\s*|arxiv\.org/(?:abs|pdf)/)?(\d{4}\.\d{4,5}(?:v\d+)?|[a-z\-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?)$", re.I)
NCT_RE = re.compile(r"\b(NCT\d{8})\b", re.I)


def kind_of(ident: str) -> tuple[str, str]:
    """What IDENT names: ("pmid" | "pmcid" | "doi" | "arxiv" | "nct", the bare id), or ("", "")."""
    s = (ident or "").strip().rstrip(".")
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.I)
    for kind, rx in (("pmcid", PMC_RE), ("pmid", PMID_RE)):
        m = rx.match(s)
        if m:
            return kind, m.group(1).upper() if kind == "pmcid" else m.group(1)
    m = re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", s)
    if m:
        return "pmid", m.group(1)
    m = NCT_RE.search(s)
    if m:
        return "nct", m.group(1).upper()
    m = DOI_RE.search(s)
    if m:
        return "doi", m.group(1).rstrip(").,;")
    m = ARXIV_RE.search(s)
    if m:
        return "arxiv", m.group(1)
    return "", ""


# ----- papers ------------------------------------------------------------

def _paper_line(i: int, p: dict) -> str:
    head = f"{i}. {p.get('title') or '(untitled)'}"
    meta = " · ".join(x for x in (_authors(p.get("authors") or []), p.get("venue", ""), str(p.get("year") or "")) if x)
    ids = " · ".join(x for x in (f"PMID {p['pmid']}" if p.get("pmid") else "", p.get("pmcid", ""),
                                 f"doi:{p['doi']}" if p.get("doi") else "", f"arXiv:{p['arxiv']}" if p.get("arxiv") else "") if x)
    flags = " · ".join(x for x in ("preprint, not peer reviewed" if p.get("preprint") else "",
                                   "open access" if p.get("open") else "",
                                   f"cited {p['cited']}x" if p.get("cited") else "") if x)
    out = [head]
    if meta:
        out.append(f"   {meta}")
    if ids:
        out.append(f"   {ids}")
    if flags:
        out.append(f"   ({flags})")
    if p.get("abstract"):
        out.append(f"   {_clip(p['abstract'], 420)}")
    if p.get("url"):
        out.append(f"   {p['url']}")
    return "\n".join(out)


def _europe_pmc(query: str, n: int, preprints_only: bool = False) -> list[dict]:
    q = f"({query}) AND SRC:PPR" if preprints_only else query
    d = _json(EUROPE_PMC + "search", {"query": q, "format": "json", "resultType": "core", "pageSize": n})
    out = []
    for r in (d.get("resultList") or {}).get("result", []):
        src = r.get("source", "")
        url = (f"https://pubmed.ncbi.nlm.nih.gov/{r['pmid']}/" if r.get("pmid") else
               f"https://doi.org/{r['doi']}" if r.get("doi") else
               f"https://europepmc.org/article/{src}/{r.get('id', '')}")
        out.append({
            "title": (r.get("title") or "").rstrip("."), "authors": [a.get("fullName", "") for a in (r.get("authorList") or {}).get("author", [])],
            "venue": ((r.get("journalInfo") or {}).get("journal") or {}).get("title") or (r.get("bookOrReportDetails") or {}).get("publisher") or
            ("preprint" if src == "PPR" else ""),
            "year": r.get("pubYear"), "pmid": r.get("pmid", ""), "pmcid": r.get("pmcid", ""), "doi": r.get("doi", ""),
            "abstract": re.sub(r"<[^>]+>", "", r.get("abstractText") or ""), "open": r.get("isOpenAccess") == "Y",
            "cited": r.get("citedByCount") or 0, "preprint": src == "PPR", "url": url,
        })
    return out


def _pubmed(query: str, n: int, sort: str = "relevance") -> list[dict]:
    d = _json(PUBMED + "esearch.fcgi", {"db": "pubmed", "term": query, "retmax": n, "retmode": "json", "sort": sort, "tool": "flash"})
    ids = (d.get("esearchresult") or {}).get("idlist", [])
    return _pubmed_records(ids)


def _pubmed_records(ids: list[str]) -> list[dict]:
    if not ids:
        return []
    root = _xml(PUBMED + "efetch.fcgi", {"db": "pubmed", "id": ",".join(ids), "retmode": "xml", "tool": "flash"})
    out = []
    for art in root.iter("PubmedArticle"):
        cit = art.find("MedlineCitation")
        a = cit.find("Article") if cit is not None else None
        if a is None:
            continue
        pmid = _text(cit.find("PMID"))
        # the article's own ids: not those of the papers in its reference list, which are ArticleIds too
        own = art.find("PubmedData/ArticleIdList")
        ids_ = {x.get("IdType"): _text(x) for x in (own.findall("ArticleId") if own is not None else [])}
        abstract = " ".join((f"{x.get('Label')}: " if x.get("Label") else "") + _text(x) for x in a.iter("AbstractText"))
        authors = []
        for au in a.iter("Author"):
            last, first = _text(au.find("LastName")), _text(au.find("Initials"))
            authors.append(f"{last} {first}".strip() or _text(au.find("CollectiveName")))
        year = _text(a.find("Journal/JournalIssue/PubDate/Year")) or _text(a.find("Journal/JournalIssue/PubDate/MedlineDate"))[:4]
        kinds = [_text(x) for x in a.iter("PublicationType")]
        out.append({
            "title": _text(a.find("ArticleTitle")).rstrip("."), "authors": authors, "venue": _text(a.find("Journal/ISOAbbreviation")) or _text(a.find("Journal/Title")),
            "year": year, "pmid": pmid, "pmcid": ids_.get("pmc", ""), "doi": ids_.get("doi", ""), "abstract": abstract,
            "open": bool(ids_.get("pmc")), "kinds": [k for k in kinds if k and k != "Journal Article"],
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        })
    return out


def _arxiv(query: str = "", n: int = 8, ids: Optional[list[str]] = None) -> list[dict]:
    params = {"id_list": ",".join(ids)} if ids else {"search_query": _arxiv_query(query), "max_results": n, "sortBy": "relevance"}
    root = _xml(ARXIV, params, "application/atom+xml")
    ns = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
    out = []
    for e in root.findall("a:entry", ns):
        aid = _text(e.find("a:id", ns)).rsplit("/abs/", 1)[-1]
        if not aid:
            continue
        out.append({
            "title": _clip(_text(e.find("a:title", ns)), 300), "authors": [_text(x.find("a:name", ns)) for x in e.findall("a:author", ns)],
            "venue": "arXiv " + (e.find("x:primary_category", ns).get("term") if e.find("x:primary_category", ns) is not None else ""),
            "year": _text(e.find("a:published", ns))[:4], "arxiv": aid, "doi": _text(e.find("x:doi", ns)),
            "abstract": _text(e.find("a:summary", ns)), "preprint": True, "open": True, "url": f"https://arxiv.org/abs/{aid}",
        })
    return out


def _arxiv_query(q: str) -> str:
    """Plain words as an arXiv search: every word, anywhere; arXiv's own syntax (ti:, au:, AND) left as written."""
    if re.search(r"\b(ti|au|abs|cat|all):|\b(AND|OR|ANDNOT)\b", q):
        return q
    words = [w for w in re.findall(r'"[^"]+"|\S+', q) if w]
    return " AND ".join(f"all:{w}" for w in words) or "all:*"


SOURCES = ("all", "pubmed", "europepmc", "preprints", "arxiv")


def papers(query: str, source: str = "all", limit: Any = 8, sort: str = "relevance") -> str:
    """Search the literature. SOURCE: all (Europe PMC, which covers PubMed and preprints, then arXiv), pubmed,
    europepmc, preprints (bioRxiv, medRxiv and others) or arxiv. SORT: relevance or date (PubMed only)."""
    query = (query or "").strip()
    if not query:
        return "Error: say what to search for."
    source = (source or "all").lower()
    if source not in SOURCES:
        return f"Error: source is one of {', '.join(SOURCES)}."
    n = _limit(limit)
    found: list[dict] = []
    said: list[str] = []
    try:
        if source == "pubmed":
            found = _pubmed(query, n, "pub_date" if sort == "date" else "relevance")
        elif source == "arxiv":
            found = _arxiv(query, n)
        elif source == "preprints":
            found = _europe_pmc(query, n, preprints_only=True)
        else:
            found = _europe_pmc(query, n)
            if source == "all":
                try:
                    extra = _arxiv(query, max(2, n // 3))
                    seen = {p.get("doi", "").lower() for p in found if p.get("doi")}
                    found += [p for p in extra if not p.get("doi") or p["doi"].lower() not in seen]
                except ScienceError as exc:
                    said.append(f"(arXiv: {exc})")
    except ScienceError as exc:
        return f"Error: {exc}"
    if not found:
        return f"No papers found for {query!r} in {source}." + (" " + " ".join(said) if said else "")
    lines = [f"{len(found)} papers for {query!r} ({source}):"] + [_paper_line(i + 1, p) for i, p in enumerate(found)] + said
    lines.append("Read one with action 'paper' and its id (PMID, PMCID, DOI or arXiv id); cite with action 'cite'.")
    return "\n\n".join(lines)


def paper(ident: str, full: bool = False) -> str:
    """One paper by its PMID, PMCID, DOI or arXiv id: its details and abstract, and with FULL its open-access text."""
    kind, bare = kind_of(ident)
    if not kind or kind == "nct":
        return "Error: give a PMID, PMCID, DOI or arXiv id (a trial's NCT number goes to action 'trial')."
    try:
        if kind == "arxiv":
            got = _arxiv(ids=[bare])
        elif kind == "pmid":
            got = _pubmed_records([bare])
        else:
            got = _europe_pmc(f"PMCID:{bare}" if kind == "pmcid" else f'DOI:"{bare}"', 1)
            if got and got[0].get("pmid"):
                more = _pubmed_records([got[0]["pmid"]])
                if more:
                    got = [{**got[0], **{k: v for k, v in more[0].items() if v}}]
    except ScienceError as exc:
        return f"Error: {exc}"
    if not got:
        return f"Error: no paper found for {ident!r}."
    p = got[0]
    out = [_paper_line(1, {**p, "abstract": ""})[3:]]
    if p.get("kinds"):
        out.append("Kind: " + ", ".join(p["kinds"]))
    out.append("Abstract: " + (_clip(p.get("abstract", ""), ABSTRACT_CHARS * 3) or "(none on record)"))
    if full:
        text = ""
        try:
            if p.get("pmcid"):
                text = full_text(p["pmcid"])
            elif kind == "arxiv":
                text = "(arXiv papers are PDFs: read it with fetch at https://arxiv.org/pdf/" + bare + ")"
        except ScienceError as exc:
            text = f"(no full text: {exc})"
        out.append(text or "(No open-access full text on record. Read the abstract, or fetch the publisher's page.)")
    return "\n".join(out)


def full_text(pmcid: str) -> str:
    """An open-access paper's text from Europe PMC, section by section, clipped."""
    root = _xml(EUROPE_PMC + f"{pmcid}/fullTextXML")
    body = root.find(".//body")
    if body is None:
        raise ScienceError("no full text on record")
    parts = []
    for sec in body.iter("sec"):
        title = _text(sec.find("title"))
        paras = [_text(p) for p in sec.findall("p")]
        if paras:
            parts.append((f"## {title}\n" if title else "") + "\n".join(paras))
    text = "\n\n".join(parts) or _text(body)
    refs = len(list(root.iter("ref")))
    clipped = len(text) > FULL_TEXT_CHARS
    return f"Full text ({pmcid}, {refs} references):\n\n" + text[:FULL_TEXT_CHARS] + ("\n\n[... clipped: ask for a section by name to read more]" if clipped else "")


def related(ident: str, limit: Any = 8) -> str:
    """Papers PubMed counts as most like this one."""
    kind, bare = kind_of(ident)
    try:
        if kind != "pmid":
            got = _europe_pmc(f'DOI:"{bare}"' if kind == "doi" else f"PMCID:{bare}", 1) if kind in ("doi", "pmcid") else []
            bare = got[0].get("pmid") if got else ""
            if not bare:
                return "Error: related papers need a paper in PubMed: give its PMID, PMCID or DOI."
        d = _json(PUBMED + "elink.fcgi", {"dbfrom": "pubmed", "db": "pubmed", "id": bare, "cmd": "neighbor_score", "retmode": "json", "tool": "flash"})
        links = []
        for ls in (d.get("linksets") or []):
            for db in ls.get("linksetdbs", []):
                if db.get("linkname") == "pubmed_pubmed":
                    links = [str(x.get("id") if isinstance(x, dict) else x) for x in db.get("links", [])]
        ids = [x for x in links if x != bare][:_limit(limit)]
        found = _pubmed_records(ids)
    except ScienceError as exc:
        return f"Error: {exc}"
    if not found:
        return "No related papers on record."
    return "\n\n".join([f"Papers most like PMID {bare}:"] + [_paper_line(i + 1, p) for i, p in enumerate(found)])


def cite(ident: str, style: str = "bibtex") -> str:
    """A citation for a paper, from its DOI (or a PMID or arXiv id, found to a DOI): bibtex, apa, vancouver, ..."""
    kind, bare = kind_of(ident)
    doi = bare if kind == "doi" else ""
    try:
        if kind in ("pmid", "pmcid"):
            got = _pubmed_records([bare]) if kind == "pmid" else _europe_pmc(f"PMCID:{bare}", 1)
            doi = got[0].get("doi", "") if got else ""
        elif kind == "arxiv":
            got = _arxiv(ids=[bare])
            plain = re.sub(r"v\d+$", "", bare)
            doi = (got[0].get("doi") if got else "") or f"10.48550/arXiv.{plain}"
        if not doi:
            return f"Error: no DOI found for {ident!r} to cite."
        style = (style or "bibtex").lower()
        if style == "bibtex":
            text = _get(f"https://doi.org/{urllib.parse.quote(doi)}", accept="application/x-bibtex").decode("utf-8", "replace")
        else:
            text = _get(f"https://doi.org/{urllib.parse.quote(doi)}", accept=f"text/x-bibliography; style={style}").decode("utf-8", "replace")
    except ScienceError as exc:
        return f"Error: {exc}"
    return text.strip() + f"\n\nhttps://doi.org/{doi}"


# ----- clinical trials ----------------------------------------------------

def _trial_line(i: int, s: dict) -> str:
    p = s.get("protocolSection", {})
    ident, stat, design = p.get("identificationModule", {}), p.get("statusModule", {}), p.get("designModule", {})
    nct = ident.get("nctId", "")
    phases = ", ".join(x.replace("PHASE", "Phase ").replace("NA", "n/a") for x in design.get("phases", []) or [])
    conds = ", ".join((p.get("conditionsModule", {}).get("conditions") or [])[:4])
    inter = ", ".join(f"{x.get('name')}" for x in (p.get("armsInterventionsModule", {}).get("interventions") or [])[:4])
    who = (p.get("sponsorCollaboratorsModule", {}).get("leadSponsor") or {}).get("name", "")
    n = (design.get("enrollmentInfo") or {}).get("count")
    start = (stat.get("startDateStruct") or {}).get("date", "")
    end = (stat.get("primaryCompletionDateStruct") or {}).get("date", "")
    out = [f"{i}. {ident.get('briefTitle', '')} ({nct})",
           "   " + " · ".join(x for x in (stat.get("overallStatus", "").replace("_", " ").lower(), phases, f"{n} enrolled" if n else "",
                                         f"{start} to {end}" if start or end else "", who) if x)]
    if conds:
        out.append(f"   Conditions: {conds}")
    if inter:
        out.append(f"   Interventions: {inter}")
    out.append(f"   https://clinicaltrials.gov/study/{nct}")
    return "\n".join(out)


def trials(query: str, status: str = "", limit: Any = 8) -> str:
    """Search ClinicalTrials.gov: a condition, drug or anything. STATUS: recruiting, completed, active, ... or empty for any."""
    query = (query or "").strip()
    if not query:
        return "Error: say what trials to look for."
    statuses = {"recruiting": "RECRUITING", "completed": "COMPLETED", "active": "ACTIVE_NOT_RECRUITING", "not yet recruiting": "NOT_YET_RECRUITING",
                "terminated": "TERMINATED", "withdrawn": "WITHDRAWN", "suspended": "SUSPENDED", "enrolling": "ENROLLING_BY_INVITATION"}
    params = {"query.term": query, "pageSize": _limit(limit), "format": "json"}
    if status:
        s = statuses.get(status.lower().strip())
        if not s:
            return f"Error: status is one of {', '.join(statuses)}."
        params["filter.overallStatus"] = s
    try:
        d = _json(TRIALS, params)
    except ScienceError as exc:
        return f"Error: {exc}"
    studies = d.get("studies") or []
    if not studies:
        return f"No trials found for {query!r}."
    return "\n\n".join([f"{len(studies)} trials for {query!r}" + (f" ({status})" if status else "") + ":"]
                       + [_trial_line(i + 1, s) for i, s in enumerate(studies)])


def trial(ident: str) -> str:
    """One trial in full: its design, arms, outcomes and eligibility."""
    kind, nct = kind_of(ident)
    if kind != "nct":
        return "Error: give the trial's NCT number, like NCT04280705."
    try:
        s = _json(f"{TRIALS}/{nct}", {"format": "json"})
    except ScienceError as exc:
        return f"Error: {exc}"
    p = s.get("protocolSection", {})
    out = [_trial_line(1, s)[3:]]
    desc = (p.get("descriptionModule") or {}).get("briefSummary", "")
    if desc:
        out.append("Summary: " + _clip(desc, 1500))
    design = p.get("designModule", {})
    info = design.get("designInfo") or {}
    bits = [info.get("allocation", ""), info.get("interventionModel", ""), (info.get("maskingInfo") or {}).get("masking", ""), info.get("primaryPurpose", "")]
    if any(bits):
        out.append("Design: " + ", ".join(b.replace("_", " ").lower() for b in bits if b))
    for arm in (p.get("armsInterventionsModule") or {}).get("armGroups", [])[:8]:
        out.append(f"Arm: {arm.get('label', '')} ({arm.get('type', '').replace('_', ' ').lower()}): {_clip(arm.get('description', ''), 300)}")
    for o in (p.get("outcomesModule") or {}).get("primaryOutcomes", [])[:6]:
        out.append(f"Primary outcome: {o.get('measure', '')} ({o.get('timeFrame', '')})")
    elig = p.get("eligibilityModule") or {}
    if elig:
        out.append(f"Eligibility: {elig.get('sex', '').lower()}, ages {elig.get('minimumAge', 'any')} to {elig.get('maximumAge', 'any')}; "
                   + _clip(elig.get("eligibilityCriteria", ""), 1200))
    results = s.get("hasResults")
    out.append("Results posted on ClinicalTrials.gov." if results else "No results posted.")
    return "\n".join(out)


# ----- compounds ---------------------------------------------------------

PROPS = ("IUPACName", "MolecularFormula", "MolecularWeight", "SMILES", "InChIKey", "XLogP", "TPSA", "HBondDonorCount",
         "HBondAcceptorCount", "RotatableBondCount", "ExactMass", "Charge")


def compound(name: str, activity: bool = True, limit: Any = 8) -> str:
    """A compound by name, CID, SMILES or ChEMBL id: its identity and properties from PubChem, Lipinski's rule of five
    checked, and (with ACTIVITY) its measured activity on targets from ChEMBL."""
    name = (name or "").strip()
    if not name:
        return "Error: say which compound."
    out = []
    chembl_id = name.upper() if re.match(r"^CHEMBL\d+$", name, re.I) else ""
    try:
        if not chembl_id:
            p = _pubchem(name)
            cid = p.get("CID")
            out.append(f"{name} (PubChem CID {cid}): {p.get('IUPACName', '')}")
            out.append(f"Formula {p.get('MolecularFormula', '?')} · MW {p.get('MolecularWeight', '?')} · exact mass {p.get('ExactMass', '?')} · charge {p.get('Charge', 0)}")
            out.append(f"SMILES {p.get('SMILES') or p.get('CanonicalSMILES', '?')}")
            out.append(f"InChIKey {p.get('InChIKey', '?')}")
            mw, logp, hbd, hba = (float(p.get(k) or 0) for k in ("MolecularWeight", "XLogP", "HBondDonorCount", "HBondAcceptorCount"))
            broken = [x for x, bad in (("MW > 500", mw > 500), ("logP > 5", logp > 5), ("H-bond donors > 5", hbd > 5), ("H-bond acceptors > 10", hba > 10)) if bad]
            out.append(f"XLogP {p.get('XLogP', '?')} · TPSA {p.get('TPSA', '?')} · donors {p.get('HBondDonorCount', '?')} · acceptors {p.get('HBondAcceptorCount', '?')} · "
                       f"rotatable bonds {p.get('RotatableBondCount', '?')}")
            out.append("Lipinski's rule of five: " + ("passes" if not broken else f"breaks {len(broken)} ({', '.join(broken)})"))
            out.append(f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}")
            out.append(f"In 3D: a molecule ui block with \"cid\": \"{cid}\"")
            if activity and p.get("InChIKey"):
                with_key = _json(CHEMBL + "molecule.json", {"molecule_structures__standard_inchi_key": p["InChIKey"], "limit": 1})
                mols = with_key.get("molecules") or []
                chembl_id = mols[0]["molecule_chembl_id"] if mols else ""
        if chembl_id and activity:
            out += _chembl_activity(chembl_id, _limit(limit))
        elif activity and not chembl_id:
            out.append("Not in ChEMBL: no measured activities on record.")
    except ScienceError as exc:
        out.append(f"Error: {exc}")
    return "\n".join(out)


def _pubchem(name: str) -> dict:
    """PubChem's properties for NAME: a CID, a name, or else a SMILES string."""
    looks_smiles = " " not in name and (re.search(r"[=#()\[\]@/\\]", name) or re.fullmatch(r"(?:Cl|Br|[BCNOSPFIbcnosp]|\d)+", name))
    ways = ["cid"] if name.isdigit() else (["smiles", "name"] if looks_smiles else ["name", "smiles"])
    for ns in ways:
        where = PUBCHEM + f"compound/{ns}/{urllib.parse.quote(name, safe='')}/property/"
        for props in (PROPS, tuple(p for p in PROPS if p != "SMILES") + ("CanonicalSMILES",)):
            try:
                d = _json(where + ",".join(props) + "/JSON")
            except ScienceError as exc:
                if "not found" in str(exc) or "404" in str(exc):
                    break
                continue
            got = ((d.get("PropertyTable") or {}).get("Properties") or [])
            if got:
                return got[0]
    raise ScienceError(f"PubChem has no compound called {name!r}")


def _chembl_activity(chembl_id: str, n: int) -> list[str]:
    m = _json(CHEMBL + f"molecule/{chembl_id}.json")
    phase = m.get("max_phase")
    out = [f"ChEMBL {chembl_id}: {m.get('pref_name') or ''}" + (f", furthest clinical phase {phase}" if phase not in (None, "") else "")
           + (" (approved drug)" if str(phase) in ("4", "4.0") else "")]
    # ranked by pChEMBL (-log10 of the molar IC50, Ki, Kd or EC50), so like is compared with like; one row a target
    d = _json(CHEMBL + "activity.json", {"molecule_chembl_id": chembl_id, "standard_type__in": "IC50,Ki,Kd,EC50", "pchembl_value__isnull": "false",
                                         "order_by": "-pchembl_value", "limit": n * 6})
    seen, rows = set(), []
    for a in d.get("activities") or []:
        target = a.get("target_pref_name") or ""
        if (not target or target.lower() in ("unchecked", "no relevant target", "non-protein target") or target in seen
                or a.get("data_validity_comment")):
            continue
        seen.add(target)
        rows.append(f"  {target} ({a.get('target_organism') or '?'}): {a.get('standard_type')} {a.get('standard_relation') or '='} "
                    f"{a.get('standard_value')} {a.get('standard_units') or ''}, pChEMBL {a.get('pchembl_value')} · {a.get('assay_chembl_id')}")
        if len(rows) >= n:
            break
    out.append("Most potent measured activities:" if rows else "No IC50/Ki/Kd/EC50 measurements on record.")
    return out + rows + [f"https://www.ebi.ac.uk/chembl/compound_report_card/{chembl_id}/"]


# ----- proteins and genes -------------------------------------------------

def protein(query: str, limit: Any = 5, sequence: bool = False) -> str:
    """Proteins and genes from UniProt (reviewed entries first): a gene name, protein name, accession, or UniProt query.
    With SEQUENCE, the first one's amino-acid sequence too."""
    query = (query or "").strip()
    if not query:
        return "Error: say which protein or gene."
    acc = re.fullmatch(r"[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9]([A-Z][A-Z0-9]{2}[0-9]){1,2}", query.upper())
    fields = "accession,id,protein_name,gene_names,organism_name,length,cc_function,cc_subcellular_location,cc_disease,xref_pdb"
    try:
        if acc:
            items = [_json(UNIPROT + query.upper() + ".json")]
        else:
            q = query if re.search(r"\w+:", query) else f"({query}) AND reviewed:true"
            d = _json(UNIPROT + "search", {"query": q, "format": "json", "size": _limit(limit, 5), "fields": fields})
            items = d.get("results") or []
            if not items and "reviewed:true" in q:
                items = (_json(UNIPROT + "search", {"query": query, "format": "json", "size": _limit(limit, 5), "fields": fields}).get("results") or [])
    except ScienceError as exc:
        return f"Error: {exc}"
    if not items:
        return f"No proteins found for {query!r}."
    out = []
    for i, e in enumerate(items, 1):
        acc_ = e.get("primaryAccession", "")
        name = (((e.get("proteinDescription") or {}).get("recommendedName") or {}).get("fullName") or {}).get("value") or \
            ((((e.get("proteinDescription") or {}).get("submissionNames") or [{}])[0].get("fullName") or {}).get("value", ""))
        genes = ", ".join(g.get("geneName", {}).get("value", "") for g in e.get("genes") or [] if g.get("geneName"))
        org = (e.get("organism") or {}).get("scientificName", "")
        length = (e.get("sequence") or {}).get("length")
        notes = {c.get("commentType"): c for c in e.get("comments") or []}
        func = " ".join(t.get("value", "") for t in (notes.get("FUNCTION") or {}).get("texts", []))
        where = ", ".join(x.get("location", {}).get("value", "") for x in (notes.get("SUBCELLULAR LOCATION") or {}).get("subcellularLocations", [])[:4])
        disease = "; ".join(d for d in (((c.get("disease") or {}).get("diseaseId") or "") for c in e.get("comments") or [] if c.get("commentType") == "DISEASE") if d)
        pdb = [x.get("id") for x in e.get("uniProtKBCrossReferences") or [] if x.get("database") == "PDB"]
        out.append(f"{i}. {name} · gene {genes or '?'} · {org} · {length} aa · UniProt {acc_}" + (" (reviewed)" if e.get("entryType", "").startswith("UniProtKB reviewed") else ""))
        if func:
            out.append(f"   Function: {_clip(func, 600)}")
        if where:
            out.append(f"   Location: {where}")
        if disease:
            out.append(f"   Disease: {_clip(disease, 300)}")
        if pdb:
            out.append(f"   Structures: {len(pdb)} in PDB, e.g. {', '.join(pdb[:5])} · predicted: https://alphafold.ebi.ac.uk/entry/{acc_}")
        out.append(f"   In 3D: a molecule ui block with " + (f"\"pdb\": \"{pdb[0]}\" (an experimental structure) or " if pdb else "")
                   + f"\"alphafold\": \"{acc_}\" (the prediction)")
        out.append(f"   https://www.uniprot.org/uniprotkb/{acc_}")
    if sequence:
        acc0 = items[0].get("primaryAccession", "")
        try:
            out.append(_get(UNIPROT + acc0 + ".fasta", accept="text/plain").decode().strip())
        except ScienceError as exc:
            out.append(f"(no sequence: {exc})")
    return "\n".join(out)


# ----- 3D structures, for the viewer ---------------------------------------

RCSB = "https://files.rcsb.org/download/"
ALPHAFOLD = "https://alphafold.ebi.ac.uk/api/prediction/"
STRUCTURE_BYTES = 30_000_000


def _cache_dir():
    from . import paths  # deferred: paths reads the environment

    d = paths.FLASH_DIR / "structures"
    d.mkdir(parents=True, exist_ok=True)
    return d


def structure(kind: str, ident: str) -> tuple[str, str]:
    """A 3D structure for the viewer, as (format, text): KIND pdb (a PDB entry), alphafold (the prediction for a
    UniProt accession), cid (PubChem's 3D conformer) or smiles. Kept on disk, so a structure seen once opens offline."""
    kind = (kind or "").lower()
    ident = (ident or "").strip()
    if kind == "pdb" and not re.fullmatch(r"[0-9][A-Za-z0-9]{3}", ident):
        raise ScienceError("a PDB id is four characters, like 1A3N")
    if kind == "alphafold" and not re.fullmatch(r"[A-Za-z0-9]{6,10}", ident):
        raise ScienceError("give a UniProt accession, like P68871")
    if kind == "cid" and not ident.isdigit():
        raise ScienceError("a PubChem CID is a number")
    if kind not in ("pdb", "alphafold", "cid", "smiles"):
        raise ScienceError("the structure is a pdb id, an alphafold accession, a cid or a smiles")
    if kind == "smiles":
        cid = str(_pubchem(ident).get("CID") or "")
        if not cid:
            raise ScienceError(f"PubChem has no compound for {ident!r}")
        kind, ident = "cid", cid
    import hashlib

    key = hashlib.sha1(f"{kind}:{ident.upper()}".encode()).hexdigest()[:16]
    fmt = "sdf" if kind == "cid" else ("cif" if kind == "pdb" else "pdb")
    cached = _cache_dir() / f"{kind}-{key}.{fmt}"
    if cached.is_file():
        return fmt, cached.read_text(encoding="utf-8", errors="replace")
    if kind == "pdb":
        text = _get(RCSB + ident.upper() + ".cif", accept="text/plain")
    elif kind == "alphafold":
        found = _json(ALPHAFOLD + ident.upper())
        url = (found[0] if isinstance(found, list) and found else {}).get("pdbUrl", "")
        if not url:
            raise ScienceError(f"AlphaFold has no prediction for {ident}")
        text = _get(url, accept="text/plain")
    else:
        try:
            text = _get(PUBCHEM + f"compound/cid/{ident}/record/SDF", {"record_type": "3d"}, accept="text/plain")
        except ScienceError:   # no 3D conformer (a salt, a big molecule): its 2D drawing
            text = _get(PUBCHEM + f"compound/cid/{ident}/record/SDF", accept="text/plain")
    if len(text) > STRUCTURE_BYTES:
        raise ScienceError("that structure is too big to show")
    out = text.decode("utf-8", "replace")
    cached.write_text(out, encoding="utf-8")
    return fmt, out


# ----- the tool -----------------------------------------------------------

ACTIONS = ("papers", "paper", "related", "cite", "trials", "trial", "compound", "protein")


def run(action: str, query: str = "", id: str = "", source: str = "all", limit: Any = None, full: Any = False,
        status: str = "", style: str = "bibtex", sort: str = "relevance", sequence: Any = False, activity: Any = True) -> str:
    """The science tool: one call, one of ACTIONS."""
    act = (action or "").strip().lower()
    truthy = lambda v: v is True or str(v).strip().lower() in ("1", "true", "yes", "on")  # noqa: E731
    key = (id or query or "").strip()
    if act == "papers":
        return papers(query or id, source, limit if limit is not None else 8, sort)
    if act == "paper":
        return paper(key, truthy(full))
    if act == "related":
        return related(key, limit if limit is not None else 8)
    if act == "cite":
        return cite(key, style)
    if act == "trials":
        return trials(query or id, status, limit if limit is not None else 8)
    if act == "trial":
        return trial(key)
    if act == "compound":
        return compound(key, True if activity is None else truthy(activity), limit if limit is not None else 8)
    if act == "protein":
        return protein(key, limit if limit is not None else 5, truthy(sequence))
    return f"Error: action is one of {', '.join(ACTIONS)}."
