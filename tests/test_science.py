"""The science tool: research databases read from recorded answers, so no test needs the network."""

import json

import pytest

from flash import science, tools

PUBMED_XML = b"""<?xml version="1.0"?><PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>33283989</PMID><Article>
<Journal><JournalIssue><PubDate><Year>2021</Year></PubDate></JournalIssue><Title>The New England journal of medicine</Title>
<ISOAbbreviation>N Engl J Med</ISOAbbreviation></Journal><ArticleTitle>CRISPR-Cas9 Gene Editing for Sickle Cell Disease.</ArticleTitle>
<Abstract><AbstractText Label="BACKGROUND">BCL11A represses fetal hemoglobin.</AbstractText><AbstractText Label="RESULTS">Both patients improved.</AbstractText></Abstract>
<AuthorList><Author><LastName>Frangoul</LastName><Initials>H</Initials></Author><Author><LastName>Altshuler</LastName><Initials>D</Initials></Author>
<Author><LastName>Cappellini</LastName><Initials>MD</Initials></Author><Author><LastName>Chen</LastName><Initials>YS</Initials></Author></AuthorList>
<PublicationTypeList><PublicationType>Journal Article</PublicationType><PublicationType>Clinical Trial</PublicationType></PublicationTypeList>
</Article></MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="pubmed">33283989</ArticleId><ArticleId IdType="doi">10.1056/NEJMoa2031054</ArticleId></ArticleIdList>
<ReferenceList><Reference><ArticleIdList><ArticleId IdType="doi">10.9999/someone-else</ArticleId><ArticleId IdType="pmc">PMC1</ArticleId></ArticleIdList></Reference></ReferenceList>
</PubmedData></PubmedArticle></PubmedArticleSet>"""

EPMC = {"resultList": {"result": [
    {"id": "PPR1", "source": "PPR", "title": "A preprint about editing.", "doi": "10.1101/2026.01.01.1", "pubYear": "2026",
     "authorList": {"author": [{"fullName": "Ada L"}]}, "abstractText": "<i>We</i> edited cells.", "isOpenAccess": "Y"},
    {"id": "33283989", "source": "MED", "pmid": "33283989", "title": "CRISPR-Cas9 Gene Editing for Sickle Cell Disease.", "doi": "10.1056/NEJMoa2031054",
     "pubYear": "2021", "journalInfo": {"journal": {"title": "N Engl J Med"}}, "citedByCount": 1420, "authorList": {"author": []}},
]}}

ARXIV = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom"><entry>
<id>http://arxiv.org/abs/1706.03762v7</id><published>2017-06-12T00:00:00Z</published><title>Attention Is All You Need</title>
<summary>The dominant sequence transduction models...</summary><author><name>Ashish Vaswani</name></author>
<arxiv:primary_category term="cs.CL"/></entry></feed>"""

TRIAL = {"protocolSection": {"identificationModule": {"nctId": "NCT03745287", "briefTitle": "CTX001 in Sickle Cell Disease"},
                             "statusModule": {"overallStatus": "COMPLETED", "startDateStruct": {"date": "2018-11-27"}},
                             "designModule": {"phases": ["PHASE2", "PHASE3"], "enrollmentInfo": {"count": 63}},
                             "conditionsModule": {"conditions": ["Sickle Cell Disease"]},
                             "armsInterventionsModule": {"interventions": [{"name": "Exa-cel"}]},
                             "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Vertex"}}}}


@pytest.fixture
def net(monkeypatch):
    """Answers by the address asked."""
    seen = []

    def get(url, params=None, accept="application/json"):
        seen.append((url, params or {}))
        if "esearch" in url:
            return json.dumps({"esearchresult": {"idlist": ["33283989"]}}).encode()
        if "efetch" in url:
            return PUBMED_XML
        if "europepmc" in url and url.endswith("search"):
            return json.dumps(EPMC).encode()
        if "arxiv" in url:
            return ARXIV
        if "clinicaltrials" in url:
            return json.dumps({"studies": [TRIAL]} if url.endswith("studies") else TRIAL).encode()
        if "pubchem" in url and "/name/" in url:
            return json.dumps({"PropertyTable": {"Properties": [{"CID": 702, "IUPACName": "ethanol", "MolecularFormula": "C2H6O",
                                                                 "MolecularWeight": "46.07", "SMILES": "CCO", "XLogP": -0.1, "HBondDonorCount": 1,
                                                                 "HBondAcceptorCount": 1, "InChIKey": "LFQ"}]}}).encode()
        if "pubchem" in url:
            raise science.ScienceError("not found")
        if "doi.org" in url:
            return b"@article{Frangoul_2021, title={CRISPR}}"
        raise science.ScienceError(f"unexpected {url}")

    monkeypatch.setattr(science, "_get", get)
    return seen


def test_what_an_id_is():
    assert science.kind_of("PMID: 33283989") == ("pmid", "33283989")
    assert science.kind_of("pmc10253463") == ("pmcid", "PMC10253463")
    assert science.kind_of("https://doi.org/10.1056/NEJMoa2031054") == ("doi", "10.1056/NEJMoa2031054")
    assert science.kind_of("arXiv:1706.03762v7") == ("arxiv", "1706.03762v7")
    assert science.kind_of("see NCT03745287") == ("nct", "NCT03745287")
    assert science.kind_of("https://pubmed.ncbi.nlm.nih.gov/33283989/") == ("pmid", "33283989")
    assert science.kind_of("hello") == ("", "")


def test_papers_from_every_source_with_preprints_marked(net):
    out = science.run("papers", query="gene editing sickle cell", limit=5)
    assert "A preprint about editing" in out and "preprint, not peer reviewed" in out
    assert "We edited cells" in out and "<i>" not in out            # markup taken out of abstracts
    assert "cited 1420x" in out and "https://pubmed.ncbi.nlm.nih.gov/33283989/" in out
    assert "Attention Is All You Need" in out and "https://arxiv.org/abs/1706.03762v7" in out


def test_a_pubmed_record_keeps_its_own_ids_not_its_references(net):
    out = science.run("paper", id="33283989")
    assert "doi:10.1056/NEJMoa2031054" in out and "someone-else" not in out and "PMC1" not in out
    assert "BACKGROUND: BCL11A represses" in out and "Frangoul H, Altshuler D, Cappellini MD et al." in out
    assert "Kind: Clinical Trial" in out


def test_trials_and_one_trial(net):
    out = science.run("trials", query="exa-cel", status="completed")
    assert "NCT03745287" in out and "Phase 2, Phase 3" in out and "63 enrolled" in out
    assert net[-1][1]["filter.overallStatus"] == "COMPLETED"
    assert science.run("trials", query="x", status="sleeping").startswith("Error")
    assert "Exa-cel" in science.run("trial", id="NCT03745287")


def test_a_compound_by_smiles_and_the_rule_of_five(net):
    out = science.run("compound", query="ethanol", activity=False)
    assert "CID 702" in out and "Lipinski's rule of five: passes" in out


def test_cite_and_errors_are_answers(net):
    assert "@article{Frangoul_2021" in science.run("cite", id="10.1056/NEJMoa2031054")
    assert science.run("paper", id="not an id").startswith("Error")
    assert science.run("explode").startswith("Error: action is one of")


def test_flash_offers_it_and_a_guide_for_it():
    assert "science" in tools.FUNCTIONS and "science" in tools.SUBAGENT_TOOL_NAMES
    assert any(t["function"]["name"] == "science" for t in tools.tools)
    from flash import skills
    assert skills.find("do-science") is not None
