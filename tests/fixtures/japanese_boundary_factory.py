"""Invented 7.782pt line pairs and five padding-only boundary conflicts."""
import re
import fitz
from tests.fixtures.japanese_overlap_factory import make_overlap_fixture,OverlapRuntime
from tests.fixtures.japanese_reading_factory import cm_font

PAIRS=[(11,11),(8,11),(41,20),(41,27),(16,27)]


def pair_texts():
    # Identifier-like labels belong to the native visual, ordinary English
    # prose below it retains real word spaces and is independently reflowed.
    first=[('vector label frame evidence '*5).replace(' ','')[:n] for n,_ in PAIRS]
    second=['nearby prose','nearby prose','nearby prose stays open',
            'nearby prose remains readable.','nearby prose remains readable.']
    result=list(zip(first,second))
    assert [(len(''.join(a.split())),len(''.join(b.split()))) for a,b in result]==PAIRS
    return result


def make_boundary_fixture(*,collision=False,inseparable=False):
    # Page 1 retains the exact 1,150-character Abstract acceptance fixture.
    # The five independently identifiable native objects live on source p.2;
    # original scientific Figure/Table/Equation are retained on source p.3.
    with fitz.open(stream=make_overlap_fixture(),filetype='pdf') as doc:
        p=doc.new_page(pno=1,width=595,height=842)
        xref=p.insert_font(fontname='CMR',fontfile=str(cm_font()))
        font=fitz.Font(fontfile=str(cm_font()))
        for i,(first,second) in enumerate(pair_texts()):
            y=130+i*100;size=7.2
            p.insert_text((100,y),first,fontname='CMR',fontsize=size)
            p.insert_text((100,y+7.782),second,fontname='CMR',fontsize=size)
            x=100+font.text_length(first,fontsize=size)-1
            p.draw_line((x,y-3),(x,y-1),width=.1)
            if collision and i==0:
                # A real native path crosses the unrelated next line. It cannot
                # be excluded by removing only the members' optional padding.
                p.draw_line((x,y-3),(x,y+8),width=.1)
        typ=doc.xref_get_key(xref,'DescendantFonts')
        if typ[0]=='array':
            descendant=int(re.findall(r'\d+',typ[1])[0])
            descriptor=int(doc.xref_get_key(descendant,'FontDescriptor')[1].split()[0])
            # A valid explicit PostScript alias, preserving the exact embedded
            # program; avoids making this boundary test depend on font aliases.
            doc.xref_set_key(xref,'BaseFont','/cmr10')
            doc.xref_set_key(descendant,'BaseFont','/cmr10')
        else:descriptor=int(doc.xref_get_key(xref,'FontDescriptor')[1].split()[0])
        doc.xref_set_key(descriptor,'Ascent','900')
        doc.xref_set_key(descriptor,'Descent','-600' if inseparable else '-350')
        doc.xref_set_key(descriptor,'FontBBox','[-100 -600 1200 900]' if inseparable else '[-100 -350 1200 900]')
        return doc.tobytes(garbage=4,deflate=True)


class BoundaryRuntime(OverlapRuntime):
    pass
