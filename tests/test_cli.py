import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from gfi_prototype import cli


class CliTests(unittest.TestCase):
    def run_server(self, args, root, interrupted=False):
        server = Mock(server_port=8504)
        if interrupted:
            server.serve_forever.side_effect = KeyboardInterrupt
        with patch.object(cli, 'Portal') as portal, patch.object(cli, 'load_project_env') as env, \
                patch.object(cli, 'create_server', return_value=server) as create, \
                contextlib.redirect_stdout(io.StringIO()):
            cli.main(args)
        env.assert_called_once_with(root)
        portal.assert_called_once_with(root)
        server.server_close.assert_called_once()
        return create

    def test_default_root_does_not_depend_on_current_directory(self):
        root = Path(cli.__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory, patch.object(cli.Path, 'cwd', return_value=Path(directory)):
            create = self.run_server(['--port', '8504'], root)
        self.assertEqual(create.call_args.args[1], root / 'web')
        self.assertEqual(create.call_args.kwargs, {'host': '127.0.0.1', 'port': 8504})

    def test_explicit_root_and_keyboard_interrupt_close_server(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'web').mkdir()
            self.run_server(['--root', directory], root, interrupted=True)

    def test_installed_assets_used_when_root_has_no_web_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assets = root / 'share/gfi-prototype/web'
            assets.mkdir(parents=True)
            with patch.object(cli.sysconfig, 'get_path', return_value=str(root)), \
                    patch.object(cli, '__file__', str(root / 'site-packages/gfi_prototype/cli.py')):
                create = self.run_server(['--root', directory], root.resolve())
            self.assertEqual(create.call_args.args[1], assets)

    def test_external_data_root_uses_source_web_without_install(self):
        source_web = Path(cli.__file__).resolve().parents[1] / 'web'
        with tempfile.TemporaryDirectory() as directory:
            create = self.run_server(['--root', directory], Path(directory).resolve())
        self.assertEqual(create.call_args.args[1], source_web)

    def test_legacy_entry_delegates_to_package_cli(self):
        import serve
        self.assertIs(serve.main, cli.main)

    def test_help_exits_before_loading_data(self):
        with patch.object(cli, 'Portal') as portal, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                cli.main(['--help'])
        self.assertEqual(caught.exception.code, 0)
        portal.assert_not_called()
