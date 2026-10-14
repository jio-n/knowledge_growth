"""Independent semantic title review; only categories/counts enter reports."""
import json
import re
from typing import Literal
from pydantic import Field,ValidationError
from .paper_brief import StrictModel
from .import_bridge.validator import _object,_constant

TITLE_REVIEW_VERSION='title-meaning-1'
CATEGORIES=('research_subject','application_domain','method_model','research_positioning','identifiers')


class Span(StrictModel):
    start:int=Field(ge=0,strict=True)
    end:int=Field(ge=1,strict=True)


class Check(StrictModel):
    category:Literal['research_subject','application_domain','method_model','research_positioning','identifiers']
    status:Literal['covered','missing','uncertain','not_applicable']
    source_spans:list[Span]=Field(max_length=8)
    translated_spans:list[Span]=Field(max_length=8)


class TitleReview(StrictModel):
    block_id:str=Field(min_length=1,max_length=64)
    verdict:Literal['complete','incomplete','uncertain']
    checks:list[Check]=Field(min_length=5,max_length=5)


class TitleReviewFailure(ValueError):
    def __init__(self,code,report=None):self.code=code;self.report=report or {'version':TITLE_REVIEW_VERSION,'status':'unverified','checks':[]}


def validate_title_review(raw,block_id,source,translated):
    try:
        if not isinstance(raw,str) or len(raw)>32000:raise ValueError()
        if raw.strip().startswith('```'):
            match=re.fullmatch(r'```(?:json)?\s*\n(.*?)\n```',raw.strip(),re.S|re.I)
            if not match:raise ValueError()
            raw=match[1]
        result=TitleReview.model_validate(json.loads(raw,object_pairs_hook=_object,parse_constant=_constant))
        if result.block_id!=block_id or sorted(c.category for c in result.checks)!=sorted(CATEGORIES):raise ValueError()
        covered=[];checks=[]
        for c in result.checks:
            for spans,text in ((c.source_spans,source),(c.translated_spans,translated)):
                if any(not 0<=s.start<s.end<=len(text) or not text[s.start:s.end].strip() for s in spans):raise ValueError()
                if len({(s.start,s.end) for s in spans})!=len(spans):raise ValueError()
            if c.status=='covered' and (not c.source_spans or not c.translated_spans):raise ValueError()
            if c.status=='missing' and (not c.source_spans or c.translated_spans):raise ValueError()
            if c.status=='not_applicable' and (c.source_spans or c.translated_spans):raise ValueError()
            if c.category=='research_subject' and c.status=='not_applicable':raise ValueError()
            if c.status=='covered':covered.extend(c.source_spans)
            checks.append({'category':c.category,'status':c.status,
                           'source_chars':sum(s.end-s.start for s in c.source_spans),
                           'translated_chars':sum(s.end-s.start for s in c.translated_spans)})
        # Every substantive source token needs an attributed counterpart. This
        # validates evidence coverage, while semantic correctness is the second
        # AI turn's judgement, not a word-replacement dictionary.
        connectors={'a','an','the','for','of','in','on','with','and','or','to','by','from','at','as','via','using'}
        unmapped=sum(m[0].lower() not in connectors and not any(s.start<=m.start() and s.end>=m.end() for s in covered)
                     for m in re.finditer(r'\w+(?:[-/]\w+)*',source))
        report={'version':TITLE_REVIEW_VERSION,'status':result.verdict,'checks':checks,'unmapped_source_tokens':unmapped}
        if any(c.status=='missing' for c in result.checks) or unmapped:
            report['status']='incomplete';raise TitleReviewFailure('title_incomplete',report)
        if result.verdict!='complete' or any(c.status=='uncertain' for c in result.checks):raise TitleReviewFailure('title_review_uncertain',report)
        return report
    except TitleReviewFailure:raise
    except (ValueError,TypeError,RecursionError,ValidationError):raise TitleReviewFailure('title_review_invalid') from None
