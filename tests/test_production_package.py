import hashlib
import importlib.util
import io
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import accounts
import app


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,Path(app.__file__).parent/path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


package=module('production_package','scripts/production-package.py')
exporter=module('production_export','scripts/export-production-base.py')


class ProductionPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name);self.source=self.folder/'source.db'
        with patch.object(app,'DB_PATH',self.source):
            app.initialize();c=app.db();manager=c.execute("SELECT id FROM user WHERE role='manager'").fetchone()[0]
            accounts.configure(c,manager,'fixture.owner','FixtureOnly2026!',manager)
            c.execute('UPDATE user SET feishu_open_id=? WHERE id=?',('private-fixture-open-id',manager))
            c.execute("INSERT INTO org_position(name) VALUES('包验证岗位')");c.commit();c.close()
        self.plain=self.folder/'plain.db';exporter.export(self.source,self.plain)
        self.files={'production-base.db':self.plain.read_bytes(),'production-base.db.report.json':Path(str(self.plain)+'.report.json').read_bytes(),'README.txt':b'Private fixture instructions'}
        self.files['SHA256SUMS']=''.join(hashlib.sha256(value).hexdigest()+'  '+name+'\n' for name,value in self.files.items()).encode()
        self.archive=self.folder/'source.tar.gz';self.archive.write_bytes(self.archive_bytes(self.files))
        self.sealed=self.folder/'release.wmt';self.key=self.folder/'release.key';self.output=self.folder/'candidate.db'

    def archive_bytes(self,files):
        stream=io.BytesIO()
        with tarfile.open(fileobj=stream,mode='w:gz') as tar:
            for name,value in files.items():
                info=tarfile.TarInfo(name);info.size=len(value);tar.addfile(info,io.BytesIO(value))
        return stream.getvalue()

    def seal(self):return package.seal(self.archive,self.sealed,self.key)

    def test_round_trip_preserves_exact_database_but_git_artifact_contains_no_plaintext(self):
        report=self.seal();ciphertext=self.sealed.read_bytes()
        self.assertTrue(ciphertext.startswith(package.MAGIC))
        for private in (b'private-fixture-open-id',b'fixture.owner',b'pbkdf2_sha256$',self.key.read_bytes().strip()):self.assertNotIn(private,ciphertext)
        restored=package.install(self.sealed,self.output,self.key)
        self.assertEqual(self.output.read_bytes(),self.plain.read_bytes());self.assertEqual(report,restored)
        for path in (self.output,self.key):self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)

    def test_wrong_key_and_modified_ciphertext_never_install_partial_database(self):
        self.seal();wrong=self.folder/'wrong.key';wrong.write_bytes(b'A'*43)
        with self.assertRaisesRegex(ValueError,'密钥不正确'):package.install(self.sealed,self.output,wrong)
        self.assertFalse(self.output.exists())
        data=bytearray(self.sealed.read_bytes());data[-1]^=1;self.sealed.write_bytes(data)
        with self.assertRaisesRegex(ValueError,'已损坏'):package.install(self.sealed,self.output,self.key)
        self.assertFalse(self.output.exists());self.assertFalse(list(self.folder.glob('.production-install-*')))

    def test_existing_database_and_wal_are_never_overwritten(self):
        self.seal();self.output.write_bytes(b'existing production data')
        with self.assertRaisesRegex(ValueError,'拒绝覆盖'):package.install(self.sealed,self.output,self.key)
        self.assertEqual(self.output.read_bytes(),b'existing production data');self.output.unlink()
        Path(str(self.output)+'-wal').write_bytes(b'existing WAL')
        with self.assertRaisesRegex(ValueError,'拒绝覆盖'):package.install(self.sealed,self.output,self.key)
        self.assertFalse(self.output.exists())

    def test_checksum_and_unexpected_archive_paths_are_rejected(self):
        changed={**self.files,'README.txt':b'tampered'}
        with self.assertRaisesRegex(ValueError,'校验失败'):package.unpack(self.archive_bytes(changed))
        with self.assertRaisesRegex(ValueError,'文件列表'):package.unpack(self.archive_bytes({**self.files,'../extra':b'extra'}))

    def test_sealing_refuses_to_overwrite_package_or_key(self):
        self.seal();before=self.sealed.read_bytes();key=self.key.read_bytes()
        with self.assertRaisesRegex(ValueError,'拒绝覆盖'):self.seal()
        self.assertEqual(self.sealed.read_bytes(),before);self.assertEqual(self.key.read_bytes(),key)


if __name__=='__main__':unittest.main()
