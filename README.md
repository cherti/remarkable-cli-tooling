# remarkable-tooling

This repository provides a couple of tools that provide easy direct interaction with a [reMarkable paper tablet](https://remarkable.com) via the shell.
It doesn't require internet, the cloud or a remarkable account, it just works over ssh via a USB-cable connection (or local Wifi, if configured and enabled).

*This software is not endorsed by reMarkable AS nor are any guarantees provided regarding suitability and correct functionality, use at your own risk.*

All scripts are currently tested with software version 2.9.1.217.

## resync.py

`resync.py` provides easy direct transfer of documents and folders of documents to a reMarkable.

### Usage

Basic usage:

    resync.py document1.pdf another_document.epub folder_with_documents ...

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


### Prequisites

  * Python 3.6+
  * Functioning ssh-access to the device
  * optional: Python's `termcolor`-module to add color to the dry-run output

## reclean.py

`reclean.py` will clean up deleted files on your remarkable, i.e. files that are gone from trash by emptying it. Due to the reMarkable typically needing to sync this action with the reMarkable cloud, these files only actually get deleted after their deletion has been synced to the cloud. If no reMarkable account is configured, this is never, hence they indefinitely stay on the device. `reclean.py` cleans those.

`reclean.py` also searches for orphaned documents, i.e. documents that are missing their metadata and are, as a consequence never picked up by the reMarkable UI (and they don't have a deleted flag either, as this would be noted in said metadata). Those files are cleaned up as well, if the user desires.

### Usage

	usage: reclean.py [-h] [-r <IP or hostname>] [--dry-run]
	
	Clean deleted files from your reMarkable
	
	optional arguments:
	  -h, --help            show this help message and exit
	  -r <IP or hostname>, --remote-address <IP or hostname>
	                        remote address of the reMarkable
	  --dry-run             Don't actually clean files, just show what would be done

### Prequisites
  * Python 3.6+
  * Functioning ssh-access to the device

Nothing needs to be installed on the remarkable.


## Credits

These scripts are inspired by [repush.sh](https://github.com/reHackable/scripts).
