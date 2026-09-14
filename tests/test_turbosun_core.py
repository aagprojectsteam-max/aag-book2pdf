import csv,io,json
from pathlib import Path
import pytest
from PIL import Image
from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
from book2pdf.turbosun.core import RecoveryError,catalogue,decrypt_record,inspect_tiff,key_for,main,recover_bundle,validate_pixels

FIELD='abcdefghijklmnopqrstuvwxyz1234'

def picture():
    image=Image.new('1',(73,59),1)
    for x in range(10,30):
        for y in range(20,40):image.putpixel((x,y),0)
    out=io.BytesIO();image.save(out,format='TIFF',compression='group4');return out.getvalue()

def encrypt(clear,key):
    r=len(clear)%16;padded=clear+b'\0'*(15-r)+bytes([r]);ctx=Cipher(algorithms.AES(key),modes.ECB()).encryptor()
    return ctx.update(padded)+ctx.finalize()

def write_csv(path,rows):
    with path.open('w') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)

def fixture(tmp, bias=124):
    source=tmp/'source';source.mkdir();(source/'db').mkdir();(source/'db/tree_0.124').write_bytes(b'\0\1\0\0Standard Jet DB\0'+b'\0'*20)
    metadata=tmp/'metadata';metadata.mkdir();filename='testa_ספר.tif';path=source/'Files_01/0001'/filename;path.parent.mkdir(parents=True)
    clear=picture();cipher=encrypt(clear,key_for(filename,FIELD));path.write_bytes(cipher*2)
    write_csv(metadata/'tree_0__a01_Text_Basis.csv',[{'F902':1,'F903':2,'F904':'0001','F905':filename,'TB_IndexCounter':1,'TB_R_Number':1,'F910':FIELD}])
    write_csv(metadata/'tree_0__a01_Text_F001.csv',[{'TF_F001_F1':i+1,'TF_F001_F2':1 if i==0 else len(cipher)+bias+1,'TF_F001_F3':len(cipher)+bias,'TF001_IndexCounter':i+1,'TF_ptrBasis':1} for i in range(2)])
    return source,metadata,path,clear

def test_original_tiff_and_independent_decoders():
    clear=picture();assert inspect_tiff(clear)[0]['width']==73;assert validate_pixels(clear)['pixels_equal']

@pytest.mark.parametrize('length',[1,15,16,17,32,33])
def test_remainder_padding_round_trip(length):
    key=bytes(range(32));clear=bytes(range(length));assert decrypt_record(encrypt(clear,key),key)==clear

def test_wrong_key_and_truncated_cipher_rejected():
    cipher=encrypt(picture(),bytes(32))
    with pytest.raises(RecoveryError):decrypt_record(cipher[:-1],bytes(32))
    with pytest.raises(RecoveryError):inspect_tiff(decrypt_record(cipher,bytes([1])*32))

def test_corrupted_ifd_and_trailing_data_rejected():
    clear=picture()
    with pytest.raises(RecoveryError):inspect_tiff(clear[:4]+b'\xff'*4+clear[8:])
    with pytest.raises(RecoveryError):inspect_tiff(clear+b'unmapped bytes')

def test_key_is_case_normalized_and_file_specific():
    assert key_for('TESTa_ספר.tif',FIELD)==key_for('testa_ספר.tif',FIELD)
    assert key_for('other_ספר.tif',FIELD)!=key_for('testa_ספר.tif',FIELD)

@pytest.mark.parametrize('bias',[0,124,300])
def test_generic_bias_source_unchanged_and_exact_extraction(tmp_path,bias):
    source,meta,path,clear=fixture(tmp_path,bias);original=path.read_bytes()
    bundles,warnings=catalogue(source,metadata_dir=meta);assert not warnings;assert bundles[0]['index_bias']==bias
    out=tmp_path/'out';out.mkdir();result=recover_bundle(bundles[0],out)
    assert len(result['pages'])==2
    for page in result['pages']:assert (out/page['output']).read_bytes()==clear
    assert path.read_bytes()==original

def test_existing_output_and_output_inside_source_protected(tmp_path):
    source,meta,path,clear=fixture(tmp_path);out=tmp_path/'out';out.mkdir();sentinel=out/'sentinel';sentinel.write_text('preserve')
    assert main([str(source),'--metadata-dir',str(meta),'-o',str(out)])==2
    assert sentinel.read_text()=='preserve'
    assert main([str(source),'--metadata-dir',str(meta),'-o',str(source/'recovered')])==2
    assert not (source/'recovered').exists()

def test_missing_page_index_refuses_recovery(tmp_path):
    source,meta,path,clear=fixture(tmp_path)
    p=meta/'tree_0__a01_Text_F001.csv';rows=list(csv.DictReader(p.open()));rows[1]['TF_F001_F1']='3';write_csv(p,rows)
    with pytest.raises(RecoveryError,match='page sequence'):catalogue(source,metadata_dir=meta)

def test_extra_indexed_pages_are_preserved_and_discrepancy_reported(tmp_path):
    source,meta,path,clear=fixture(tmp_path)
    p=meta/'tree_0__a01_Text_Basis.csv';rows=list(csv.DictReader(p.open()));rows[0]['F903']='1';write_csv(p,rows)
    bundles,warnings=catalogue(source,metadata_dir=meta);assert len(bundles[0]['pages'])==2;assert warnings[0]['indexed_pages']==2

