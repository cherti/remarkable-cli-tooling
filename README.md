# remarkable CLI tooling

This repository provides a couple of tools that provide easy direct interaction with a [reMarkable paper tablet](https://remarkable.com) via the shell.
It doesn't require internet, the cloud or a remarkable account, it just works over ssh via a USB-cable connection (or local Wifi, if configured and enabled, although [additional steps may be necessary](https://www.reddit.com/r/RemarkableTablet/comments/edozpq/howto_access_the_web_interface_via_ssh/)).

*This software is not endorsed by reMarkable AS nor are any guarantees provided regarding suitability and correct functionality, use at your own risk.*

All scripts are currently tested with software versions:

  * 2.9.1.217
  * 2.10.3.379
  * 2.11.0.442
  * 2.12.1.527
  * 2.12.2.573
  * 2.12.3.606
  * 2.14.3.1005

and should work under any unixoid operating system, such as Linux, BSD or MacOS/OSX. If you want to use these scripts under Windows, you likely need to resort to the Windows Subsystem for Linux to do so.
Where reMarkable-specific internals have changed, supported version numbers are tagged to specific commits, to make sure to mark version-specific verified software versions.
The latest version of this repository is typically tailored to the latest version of the reMarkable software.

## Summary

  * `resync.py`: push documents to or pull documents from the reMarkable, or to pull everything for backup purposes
  * `reclean.py`: cleanup deleted documents on the reMarkable (necessary if the cloud is not used)
  * `resign.py`: temporarily transfer documents to the reMarkable to put a signature, and pull them again once it's done

## resync.py

`resync.py` provides easy direct transfer of documents and folders of documents to a reMarkable and pull documents from it again.
It includes the possibility to synchronize entire filesystem trees between devices (with the limitation of, if desired, skipping or replacing either all files or none).
By default, files will, however, simply be added to the document tree, even if they exist.

### Usage

Basic usage:
To push documents to the remarkable, use one of

    resync.py push document1.pdf another_document.epub folder_with_documents ...
    resync.py + document1.pdf another_document.epub folder_with_documents ...

To retrieve documents from the remarkable, use one of

    resync.py pull document1.pdf some_folder/another_document folder_with_documents ...
    resync.py - document1.pdf some_folder/another_document folder_with_documents ...

To pull all documents (exclude patterns still apply), use

    resync.py backup

To pull documents or folders, the full path from the top level has to be provided; entire folders can also be pulled.

`resync.py` also provides a number of flags to select the destination folder, to skip already existing files or to overwrite them.
If in doubt, especially when pushing, use `--dry-run` to see what's going to happen beforehand.

Files are identified by their visible name and their parent folder, if this is not unambiguously possible, resync.py will error out.
By default all files will be copied anew to the remarkable, if you copy a file that is already there, you'll have it twice. See for example `-s` below for alternative behaviors.
Folders are never recreated, they are only created if they don't already exist.

For the full set of options, refer to `resync.py --help`:

	usage: resync.py [-h] [--dry-run] [-o <folder>] [-s | --overwrite | --overwrite_doc_only] [-e EXCLUDE_PATTERNS [EXCLUDE_PATTERNS ...]] [-r <IP or hostname>]
					 [--transfer-dir <directory name>] [--debug]
					 mode [documents ...]

	Push and pull files to and from your reMarkable

	positional arguments:
	  mode                  push/+, pull/- or backup
	  documents             Documents and folders to be pushed to the reMarkable

	optional arguments:
	  -h, --help            show this help message and exit
	  --dry-run             Don't actually copy files, just show what would be copied (currently push only)
	  -o <folder>, --output <folder>
							Destination for copied files, either on or off device
	  -v                    verbosity level
	  --if-exists {skip,new,replace,replace-pdf-only}
                        if the destination file already exists: *skip* pushing document (default); create a
                        separate *new* separate document under the same name; *replace* document; *replace-
                        pdf-only*: replace underlying pdf only on reMarkable, keep notes etc.
	  -e EXCLUDE_PATTERNS [EXCLUDE_PATTERNS ...], --exclude EXCLUDE_PATTERNS [EXCLUDE_PATTERNS ...]
							exclude a pattern from transfer (must be Python-regex)
	  -r <IP or hostname>, --remote-address <IP or hostname>
							remote address of the reMarkable
	  --transfer-dir <directory name>
							custom directory to render files to-be-upload
	  --debug               Render documents, but don't copy to remarkable.

If you want to test this script without the risk of messing up your documents, you can make a backup of the folder `~/.local/share/remarkable/xochitl` on the remarkable to restore if anything goes wrong.


### Prequisites

  * Python 3.6+
  * Functioning [ssh-access](https://remarkablewiki.com/tech/ssh#passwordless_login_with_ssh_keys) to the device (practically speaking passwordless via SSH keys)
  * for pull: the web-interface must be enabled (Settings > Storage > USB web interface)
  * optional: Python's `termcolor`-module to add color to the dry-run output

## reclean.py

`reclean.py` will clean up deleted files on your remarkable, i.e. files that are gone from trash by emptying it. Due to the reMarkable typically needing to sync this action with the reMarkable cloud, these files only actually get deleted after their deletion has been synced to the cloud. If no reMarkable account is configured, this is never, hence they indefinitely stay on the device. `reclean.py` takes that place, cleaning up those leftovers to free the space on the remarkable again.

`reclean.py` also searches for orphaned documents, i.e. documents that are missing their metadata and are, as a consequence never picked up by the reMarkable UI (and they don't have a deleted flag either, as this would be noted in said metadata). Those files are cleaned up as well, if the user desires.

### Usage

Basic usage:

    reclean.py

For the full set of options, refer to `reclean.py --help`:

	usage: reclean.py [-h] [-r <IP or hostname>] [--dry-run]
	
	Clean deleted files from your reMarkable
	
	optional arguments:
	  -h, --help            show this help message and exit
	  -r <IP or hostname>, --remote-address <IP or hostname>
	                        remote address of the reMarkable
	  --dry-run             Don't actually clean files, just show what would be done

### Prequisites
  * Python 3.6+
  * Functioning [ssh-access](https://remarkablewiki.com/tech/ssh#passwordless_login_with_ssh_keys) to the device (practically speaking passwordless via SSH keys)

Nothing needs to be installed on the remarkable.


## resign.py

`resign.py` will transfer documents onto the reMarkable, so you can sign them, and then pulls them again with the signature on it and deletes the document from the reMarkable again.
It requires `resync.py` to be available, so if you name `resync.py` or put it into a location that is not in `PATH`, you need to adjust the variable at the very top in `resign.py`.

### Usage

Basic usage:

    resign.py document1.pdf [document2.pdf ...]

For the full set of options, refer to `resign.py --help`:

	usage: resign.py [-h] [-r <IP or hostname>] [documents ...]
	
	Relay documents over your reMarkable for signing
	
	positional arguments:
	  documents             Documents and folders to be signed
	
	optional arguments:
	  -h, --help            show this help message and exit
	  -r <IP or hostname>, --remote-address <IP or hostname>
                        remote address of the reMarkable

### Prequisites
  * Python 3.6+
  * Functioning [ssh-access](https://remarkablewiki.com/tech/ssh#passwordless_login_with_ssh_keys) to the device (practically speaking passwordless via SSH keys)

Nothing needs to be installed on the remarkable.

## Limitations

At this time, this set of tools imposes two limitations on file names in your reMarkable:

  * No `/` in file names, as this is the filesystem separator, messing with the proper mapping of directory trees within your remarkable onto your filesystem
  * No `'` in file names, as it messes with the remote ssh command construction.

`resync` will check for these and ignore paths that contain such characters.

## Credits

These scripts are inspired by [reHackable/scripts](https://github.com/reHackable/scripts).
