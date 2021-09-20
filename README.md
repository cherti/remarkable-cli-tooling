# resync.py

This is a tool that provides easy direct transfer of documents to a [reMarkable](https://remarkable.com) by commandline over ssh via a USB-connection, no internet required.

*This software is not endorsed by reMarkable AS nor are any guarantees provided regarding suitability and correct functionality, use at your own risk.*

Currently tested with software version 2.9.1.217.

## Usage

Basic usage:

    repush.py document1.pdf another_document.epub folder_with_documents ...

It also provides a number of flags to select the destination folder, to skip already existing files or to overwrite them.
Files are identified by their visible name and their parent folder, if this is not unambiguously possible, resync.py will error out.
By default all files will be copied anew to the remarkable (unless for example `-s` is specified). Folders are never recreated, they are only created if they don't already exist.


	usage: resync.py [-h] [-o <folder>] [-r <IP or hostname>] [--transfer-dir <directory name>] [--dry-run] [-s] [--overwrite] [--overwrite_doc_only] [--debug] [documents ...]
	
	Push files to your reMarkable
	
	positional arguments:
	  documents
	
	optional arguments:
	  -h, --help            show this help message and exit
	  -o <folder>, --output <folder>
	  -r <IP or hostname>, --remote-address <IP or hostname>
	                        remote address of the reMarkable
	  --transfer-dir <directory name>
	  --dry-run             Don't actually copy files, just show what would be copied
	  -s, --skip-existing-files
	                        Don't copy additional versions of existing files
	  --overwrite           Overwrite existing files with a new version (potentially destructive)
	  --overwrite_doc_only  Overwrite the underlying file only, keep notes and such (potentially destructive)
	  --debug               Render documents, but don't copy to remarkable.


If you want to test this script without the risk of messing up your documents, you can make a backup of the folder `~/.local/share/remarkable/xochitl` on the remarkable to restore if anything goes wrong.


## Prequisites

  * Python 3.6+
  * Functioning ssh-access to the device
  * optional: Python's `termcolor`-module to add color to the dry-run output

Nothing needs to be installed on the remarkable.


## Credits

This script is inspired by [repush.sh](https://github.com/reHackable/scripts/blob/master/host/repush.sh), but provides a superset of its features.
