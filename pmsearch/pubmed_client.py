from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from http.client import IncompleteRead
from datetime import date, datetime, timedelta
from io import BytesIO
from typing import Any

try:
    import ssl
    import certifi

    ssl._create_default_https_context = lambda: ssl.create_default_context(
        cafile=certifi.where()
    )
except Exception:
    pass

from Bio import Entrez

from .country_infer import infer_country_from_affiliation


def dedupe_pmids_preserve_order(pmids: list[str]) -> list[str]:
    """
    One row per PMID, first occurrence wins (keeps ESearch order).

    PubMed ``IdList`` is normally unique; this guards against rare API quirks and
    duplicate IDs in the same response when combining clauses (e.g. author OR queries).
    """
    seen: set[str] = set()
    out: list[str] = []
    for p in pmids:
        s = str(p).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _text(elem: ET.Element | None) -> str:
    if elem is None:
        return ""
    parts: list[str] = []
    if elem.text:
        parts.append(elem.text.strip())
    for child in elem:
        parts.append(_text(child))
        if child.tail:
            parts.append(child.tail.strip())
    return " ".join(p for p in parts if p).strip()


def _collect_abstract(article: ET.Element) -> str:
    abstract = None
    for child in article:
        if _local_name(child.tag) == "Abstract":
            abstract = child
            break
    if abstract is None:
        return ""
    texts: list[str] = []
    for node in abstract:
        if _local_name(node.tag) != "AbstractText":
            continue
        label = node.attrib.get("Label", "")
        chunk = _text(node)
        if not chunk:
            continue
        if label:
            texts.append(f"{label}: {chunk}")
        else:
            texts.append(chunk)
    return "\n".join(texts).strip()


def _pub_date_str(medline_citation: ET.Element) -> str:
    article = None
    for child in medline_citation:
        if _local_name(child.tag) == "Article":
            article = child
            break
    if article is None:
        return ""
    journal = None
    for child in article:
        if _local_name(child.tag) == "Journal":
            journal = child
            break
    if journal is None:
        return ""
    issue = None
    for child in journal:
        if _local_name(child.tag) == "JournalIssue":
            issue = child
            break
    if issue is None:
        return ""
    pub_date = None
    for child in issue:
        if _local_name(child.tag) == "PubDate":
            pub_date = child
            break
    if pub_date is None:
        return ""
    year = month = day = ""
    for child in pub_date:
        ln = _local_name(child.tag)
        if ln == "Year":
            year = (child.text or "").strip()
        elif ln == "Month":
            month = (child.text or "").strip()
        elif ln == "Day":
            day = (child.text or "").strip()
        elif ln == "MedlineDate":
            return (child.text or "").strip()
    parts = [p for p in (year, month, day) if p]
    return " ".join(parts) if parts else ""


def _author_affiliation_pairs(article: ET.Element) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    author_list = None
    for child in article:
        if _local_name(child.tag) == "AuthorList":
            author_list = child
            break
    if author_list is None:
        return pairs
    for author in author_list:
        if _local_name(author.tag) != "Author":
            continue
        name = ""
        collective = None
        last = fore = ""
        for node in author:
            ln = _local_name(node.tag)
            if ln == "LastName":
                last = (node.text or "").strip()
            elif ln == "ForeName":
                fore = (node.text or "").strip()
            elif ln == "Initials":
                if not fore:
                    fore = (node.text or "").strip()
            elif ln == "CollectiveName":
                collective = (node.text or "").strip()
        if collective:
            name = collective
        else:
            name = " ".join(p for p in (fore, last) if p).strip() or last or fore
        affs: list[str] = []
        for node in author:
            if _local_name(node.tag) != "AffiliationInfo":
                continue
            for sub in node:
                if _local_name(sub.tag) == "Affiliation":
                    t = (sub.text or "").strip()
                    if t:
                        affs.append(t)
        aff = "; ".join(affs)
        if name:
            pairs.append((name, aff))
    return pairs


def _corresponding_author_and_aff(pairs: list[tuple[str, str]]) -> tuple[str, str]:
    """PubMed 通常不标注通讯作者；生物医学领域常见约定为最后一位作者。"""
    if not pairs:
        return "", ""
    last_name, last_aff = pairs[-1]
    if last_aff:
        return last_name, last_aff
    for name, aff in reversed(pairs):
        if aff:
            return name, aff
    return last_name, last_aff


def _journal_title(article: ET.Element) -> str:
    journal = None
    for child in article:
        if _local_name(child.tag) == "Journal":
            journal = child
            break
    if journal is None:
        return ""
    for child in journal:
        if _local_name(child.tag) == "Title":
            return _text(child)
    return ""


def _issn_linking(medline_citation: ET.Element) -> str:
    for child in medline_citation:
        if _local_name(child.tag) != "MedlineJournalInfo":
            continue
        for sub in child:
            if _local_name(sub.tag) == "ISSNLinking":
                return (sub.text or "").strip()
    return ""


def _journal_issns(article: ET.Element) -> list[str]:
    journal = None
    for child in article:
        if _local_name(child.tag) == "Journal":
            journal = child
            break
    if journal is None:
        return []
    out: list[str] = []
    for child in journal:
        if _local_name(child.tag) == "ISSN":
            t = (child.text or "").strip()
            if t:
                out.append(t)
    return out


def _issn_field(medline_citation: ET.Element, article: ET.Element) -> str:
    link = _issn_linking(medline_citation)
    issns = _journal_issns(article)
    parts: list[str] = []
    if link:
        parts.append(link)
    for x in issns:
        if x and x not in parts:
            parts.append(x)
    return "; ".join(parts)


def _mesh_terms(medline_citation: ET.Element) -> list[str]:
    mesh_terms: list[str] = []
    for child in medline_citation:
        if _local_name(child.tag) == "MeshHeadingList":
            for mh in child:
                if _local_name(mh.tag) != "MeshHeading":
                    continue
                for sub in mh:
                    if _local_name(sub.tag) == "DescriptorName":
                        t = (sub.text or "").strip()
                        if t:
                            mesh_terms.append(t)
    return mesh_terms


def _author_keywords_from_element(parent: ET.Element) -> list[str]:
    """解析 KeywordList/Keyword。PubMed 常见位置是 MedlineCitation 下（与 Article 同级），少数在 Article 内。"""
    out: list[str] = []
    for child in parent:
        if _local_name(child.tag) != "KeywordList":
            continue
        for kw in child:
            if _local_name(kw.tag) != "Keyword":
                continue
            t = _text(kw)
            if t:
                out.append(t)
    return out


def _author_keywords(medline_citation: ET.Element, article: ET.Element) -> list[str]:
    collected: list[str] = []
    seen: set[str] = set()
    for parent in (medline_citation, article):
        for t in _author_keywords_from_element(parent):
            if t not in seen:
                seen.add(t)
                collected.append(t)
    return collected


def _mesh_and_keywords(medline_citation: ET.Element, article: ET.Element) -> str:
    mesh_terms = _mesh_terms(medline_citation)
    author_kw = _author_keywords(medline_citation, article)
    combined = author_kw + [m for m in mesh_terms if m not in author_kw]
    return "; ".join(combined)


def _parse_article(pubmed_article: ET.Element) -> dict[str, Any] | None:
    medline = None
    for child in pubmed_article:
        if _local_name(child.tag) == "MedlineCitation":
            medline = child
            break
    if medline is None:
        return None
    pmid = ""
    for child in medline:
        if _local_name(child.tag) == "PMID":
            pmid = (child.text or "").strip()
            break
    article = None
    for child in medline:
        if _local_name(child.tag) == "Article":
            article = child
            break
    if article is None:
        return None
    title = ""
    for child in article:
        if _local_name(child.tag) == "ArticleTitle":
            title = _text(child)
            break
    pairs = _author_affiliation_pairs(article)
    corr_name, corr_aff = _corresponding_author_and_aff(pairs)
    journal_title = _journal_title(article)
    issn_field = _issn_field(medline, article)
    inst_country = infer_country_from_affiliation(corr_aff)
    kw = _mesh_and_keywords(medline, article)
    abstract = _collect_abstract(article)
    pub_date = _pub_date_str(medline)
    pubmed_url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else ""
    return {
        "PMID": pmid,
        "PubMed URL": pubmed_url,
        "Title": title,
        "Publication Date": pub_date,
        "Journal": journal_title,
        "ISSN": issn_field,
        "Corresponding Author": corr_name,
        "Corresponding Author Affiliation": corr_aff,
        "Institution Country": inst_country,
        "Keywords": kw,
        "Abstract": abstract,
    }


def parse_pubmed_xml_batch(xml_bytes: bytes) -> list[dict[str, Any]]:
    root = ET.parse(BytesIO(xml_bytes)).getroot()
    rows: list[dict[str, Any]] = []
    for pubmed_article in root:
        if _local_name(pubmed_article.tag) != "PubmedArticle":
            continue
        row = _parse_article(pubmed_article)
        if row:
            rows.append(row)
    return rows


def extract_keyword_lists_per_article(
    xml_bytes: bytes,
) -> list[tuple[str, list[str], list[str]]]:
    """Parse one efetch XML chunk: list of (pmid, author_keywords, mesh_descriptors)."""
    root = ET.parse(BytesIO(xml_bytes)).getroot()
    out: list[tuple[str, list[str], list[str]]] = []
    for pubmed_article in root:
        if _local_name(pubmed_article.tag) != "PubmedArticle":
            continue
        medline = None
        article = None
        for child in pubmed_article:
            if _local_name(child.tag) == "MedlineCitation":
                medline = child
                break
        if medline is None:
            continue
        pmid = ""
        for child in medline:
            if _local_name(child.tag) == "PMID":
                pmid = (child.text or "").strip()
                break
        for child in medline:
            if _local_name(child.tag) == "Article":
                article = child
                break
        if article is None:
            continue
        mesh = _mesh_terms(medline)
        auth = _author_keywords(medline, article)
        out.append((pmid, auth, mesh))
    return out


def fetch_all_keyword_lists(
    pmids: list[str],
    *,
    email: str,
    tool: str,
    api_key: str | None,
    batch_size: int = 80,
    delay_s: float = 0.2,
) -> list[tuple[str, list[str], list[str]]]:
    """efetch all PMIDs; parse PMID + author keywords + MeSH per article."""
    triples: list[tuple[str, list[str], list[str]]] = []
    for i in range(0, len(pmids), batch_size):
        batch = pmids[i : i + batch_size]
        xml_bytes = _efetch_xml_with_retries(
            batch,
            email=email,
            tool=tool,
            api_key=api_key,
        )
        triples.extend(extract_keyword_lists_per_article(xml_bytes))
        if i + batch_size < len(pmids):
            time.sleep(delay_s)
    return triples


def build_search_term(
    keywords: list[str],
    *,
    join_between: str = "OR",
    freeform: bool = False,
) -> str:
    """
    PubMed ESearch ``term`` string.

    * **freeform=True:** one raw query string (field tags, nested parentheses, NOT, filters).
      No automatic wrapping — sent as-is to NCBI.

    * **freeform=False:** each non-empty line is parenthesized as ``(...)`` and combined with
      **join_between**: ``OR``, ``AND``, or ``NOT``. For ``NOT``, clauses chain left-to-right
      as ``(a) NOT (b) NOT (c)``, matching typical PubMed boolean behavior.
    """
    if not keywords:
        raise ValueError(
            "No keywords configured. Edit data/keywords.json or use: keywords add ..."
        )
    if freeform:
        q = keywords[0].strip() if keywords else ""
        if not q:
            raise ValueError("Empty pubmed query (freeform).")
        if len(keywords) > 1:
            raise ValueError("Freeform mode expects a single query string.")
        return q

    jb = str(join_between or "OR").strip().upper()
    if jb not in ("AND", "OR", "NOT"):
        jb = "OR"
    parts: list[str] = []
    for kw in keywords:
        q = kw.strip()
        if not q:
            continue
        parts.append(f"({q})")
    if not parts:
        raise ValueError("No valid keywords after trimming empty entries.")
    if jb == "NOT":
        if len(parts) < 2:
            raise ValueError(
                "join NOT requires at least two clauses (PubMed: A NOT B [NOT C ...])."
            )
        result = parts[0]
        for p in parts[1:]:
            result = f"{result} NOT {p}"
        return result
    sep = f" {jb} "
    return sep.join(parts)


def date_window_today(days: int = 15) -> tuple[str, str]:
    """返回 mindate/maxdate 字符串，以「今天」为区间终点，向前 days 天（含）。"""
    end = date.today()
    start = end - timedelta(days=days - 1)
    return start.strftime("%Y/%m/%d"), end.strftime("%Y/%m/%d")


def _parse_one_pdat_date(chunk: str) -> date:
    s = chunk.strip()
    if not s:
        raise ValueError("empty date")
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognized date {chunk!r} (use YYYY-MM-DD or YYYY/MM/DD)")


def parse_pdat_range(s: str) -> tuple[str, str, int]:
    """
    Parse a fixed publication-date window for ESearch ``mindate``/``maxdate``.

    Accepts ``START:END`` or ``START..END``. Dates may use ``-``, ``/``, or ``.`` as separators.

    Returns ``(mindate, maxdate, inclusive_days)`` with dates formatted as ``%Y/%m/%d`` for NCBI.
    """
    raw = (s or "").strip()
    if not raw:
        raise ValueError("empty range")
    if ".." in raw:
        left, right = raw.split("..", 1)
    elif ":" in raw:
        left, right = raw.split(":", 1)
    else:
        raise ValueError("expected START:END or START..END")
    start_d = _parse_one_pdat_date(left)
    end_d = _parse_one_pdat_date(right)
    if end_d < start_d:
        raise ValueError("range end is before start")
    inclusive = (end_d - start_d).days + 1
    return start_d.strftime("%Y/%m/%d"), end_d.strftime("%Y/%m/%d"), inclusive


def search_pmids(
    term: str,
    mindate: str,
    maxdate: str,
    *,
    email: str,
    tool: str,
    api_key: str | None,
) -> list[str]:
    Entrez.email = email or None
    Entrez.tool = tool or "pmsearch_tool"
    if api_key:
        Entrez.api_key = api_key
    handle = Entrez.esearch(
        db="pubmed",
        term=term,
        retmax=100000,
        mindate=mindate,
        maxdate=maxdate,
        datetype="pdat",
    )
    try:
        record = Entrez.read(handle)
    finally:
        handle.close()
    id_list = record.get("IdList") or []
    raw = [str(i) for i in id_list]
    return dedupe_pmids_preserve_order(raw)


def _efetch_xml_with_retries(
    batch: list[str],
    *,
    email: str,
    tool: str,
    api_key: str | None,
    retries: int = 4,
    delay_s: float = 0.5,
) -> bytes:
    Entrez.email = email or None
    Entrez.tool = tool or "pmsearch_tool"
    if api_key:
        Entrez.api_key = api_key
    last_err: Exception | None = None
    for attempt in range(retries):
        handle = Entrez.efetch(
            db="pubmed",
            id=batch,
            rettype="xml",
            retmode="xml",
        )
        try:
            return handle.read()
        except (IncompleteRead, OSError, ValueError) as e:
            last_err = e
            time.sleep(delay_s * (attempt + 1))
        finally:
            handle.close()
    assert last_err is not None
    raise last_err


def fetch_all_pubmed_records(
    pmids: list[str],
    *,
    email: str,
    tool: str,
    api_key: str | None,
    batch_size: int = 80,
    delay_s: float = 0.2,
) -> list[dict[str, Any]]:
    """分批 efetch 并解析；每批为独立 XML，不可拼接。"""
    rows: list[dict[str, Any]] = []
    for i in range(0, len(pmids), batch_size):
        batch = pmids[i : i + batch_size]
        xml_bytes = _efetch_xml_with_retries(
            batch,
            email=email,
            tool=tool,
            api_key=api_key,
        )
        rows.extend(parse_pubmed_xml_batch(xml_bytes))
        if i + batch_size < len(pmids):
            time.sleep(delay_s)
    return rows
