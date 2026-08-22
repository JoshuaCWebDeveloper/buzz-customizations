# base-prompt

This customization provides the managed Buzz base prompt as `base_prompt.md`.

To deploy it to the live host path:

```sh
python3 deploy.py install
```

For isolated validation or staging, override the destination:

```sh
python3 deploy.py install --destination /path/to/staging/base_prompt.md
```

The deploy action copies the package file byte-for-byte. It does not change the
prompt content or create a backup. To remove the deployed file, run:

```sh
python3 deploy.py uninstall --destination /path/to/staging/base_prompt.md
```

Uninstall removes only the selected destination file.

Run package tests from the repository root with:

```sh
python3 -m unittest discover -s packages/base-prompt -p 'test_*.py'
```
