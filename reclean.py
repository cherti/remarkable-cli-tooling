#!/usr/bin/env python3

import sys
import json
import argparse
import subprocess
import tempfile
import pathlib
import io
import tqdm

ssh_socketfile = '/tmp/remarkable-push.socket'

parser = argparse.ArgumentParser(description='Clean deleted files from your reMarkable')
parser.add_argument('-r', '--remote-address',
                    action='store',
                    default='10.11.99.1',
                    dest='ssh_destination',
                    metavar='<IP or hostname>',
                    help='remote address of the reMarkable')
parser.add_argument('-n', '--dry-run',
                    dest='dryrun',
                    action='store_true',
                    default=False,
                    help="Don't actually clean files, just show what would be done")
parser.add_argument('-v', dest='verbosity', action='count', default=0,
                    help='verbosity level')

args = parser.parse_args()


ssh_command = f'ssh -o PubkeyAcceptedKeyTypes=+ssh-rsa -o HostKeyAlgorithms=+ssh-rsa -S {ssh_socketfile}'

def ssh(arg,dry=False):
    if args.verbosity >= 1:
        print(f'{ssh_command} root@{args.ssh_destination} {arg}')
    if not dry:
        return subprocess.getoutput(f'{ssh_command} root@{args.ssh_destination} {arg}')


# from https://stackoverflow.com/questions/6886283/how-i-can-i-lazily-read-multiple-json-values-from-a-file-stream-in-python
def stream_read_json(f):
    start_pos = 0
    while True:
        try:
            obj = json.load(f)
            yield obj
            return
        except json.JSONDecodeError as e:
            f.seek(start_pos)
            json_str = f.read(e.pos)
            if json_str == '':
                return
            obj = json.loads(json_str)
            start_pos += e.pos
            yield obj


metadata_by_uuid = {}
metadata_by_name = {}
metadata_by_parent = {}
metadata_by_name_and_parent = {}

def retrieve_metadata():
    """
    retrieves all metadata from the device
    """
    print("retrieving metadata...")

    paths = ssh(f'"ls -1 .local/share/remarkable/xochitl/*.metadata"').split("\n")
    with io.StringIO(ssh(f'"cat .local/share/remarkable/xochitl/*.metadata"')) as f:
        for path, metadata in tqdm.tqdm(zip(paths, stream_read_json(f)), total=len(paths)):
            path = pathlib.Path(path)
            if metadata['deleted'] or metadata['parent'] == 'trash':
                continue
            # metadata["uuid"] = path.stem
            uuid = path.stem
            metadata_by_uuid[uuid]                    = metadata

            if metadata["visibleName"] not in metadata_by_name:
                metadata_by_name[metadata["visibleName"]] = dict()
            metadata_by_name[metadata["visibleName"]][uuid] = metadata

            if metadata["parent"] not in metadata_by_parent:
                metadata_by_parent[metadata["parent"]] = dict()
            metadata_by_parent[metadata["parent"]][uuid] = metadata

            if (metadata["visibleName"], metadata["parent"]) in metadata_by_name_and_parent:
                raise FileCollision(f'Same file name {metadata["visibleName"]} under the same parent, not supported! Remove the file!')
            metadata_by_name_and_parent[(metadata["visibleName"], metadata["parent"])] = (uuid, metadata)
    pass


def get_metadata_by_uuid(u):
    """
    retrieves metadata for a given document identified by its uuid
    """
    if u in metadata_by_uuid:
        return metadata_by_uuid[u]
    else:
        return None


def get_metadata_by_name(name):
    """
    retrieves metadata for all given documents that have the given name set as visibleName
    """
    if name in metadata_by_name:
        return metadata_by_name[name]
    else:
        return None


def get_metadata_by_parent(parent):
    """
    retrieves metadata for all given documents that have the given parent
    """
    if parent in metadata_by_parent:
        return metadata_by_parent[parent]
    else:
        return {}


def get_metadata_by_name_and_parent(name, parent):
    """
    retrieves metadata for all given documents that have the given parent
    """
    if (name, parent) in metadata_by_name_and_parent:
        return metadata_by_name_and_parent[(name, parent)]
    else:
        return None



#################################
#
#   Clean up deleted files
#
#################################

def cleanup_deleted():

    metadata_uuids = set(metadata_by_uuid.keys())

    deleted_uuids = []
    limit = 10
    for u, metadata in tqdm.tqdm(metadata_by_uuid.items()):
        if metadata['deleted']:
            deleted_uuids.append(u)

    if len(deleted_uuids) == 0:
        print('No deleted files found.')
    else:
        decision = input(f'Clean up {len(deleted_uuids)} deleted files? [Y/n]')
        if decision in ['', 'y', 'Y']:
            for u in deleted_uuids:
                ssh("rm -r ~/.local/share/remarkable/xochitl/{u}*", dry=args.dryrun)

    return


#################################
#
#   Clean up orphaned files
#
#################################

def cleanup_orphaned():
    if args.dryrun:
        ssh('"for f in $(ls -1 ~/.local/share/remarkable/xochitl) ; do stem=${$(basename $f)%%.*}; if ! [ -e $stem.metadata ] ; then echo rm $stem.* ; fi ; done"')
    else:
        ssh('"for f in $(ls -1 ~/.local/share/remarkable/xochitl) ; do stem=${$(basename $f)%%.*}; if ! [ -e $stem.metadata ] ; then rm $stem.* ; fi ; done"')




ssh_connection = None
try:
    ssh_connection = subprocess.Popen(f'{ssh_command} root@{args.ssh_destination} -o ConnectTimeout=1 -M -N -q ', shell=True)

    # quickly check if we actually have a functional ssh connection (might not be the case right after an update)
    checkmsg = ssh('"/bin/true"')
    if checkmsg != "":
        print("ssh connection does not work, verify that you can manually ssh into your reMarkable. ssh itself commented the situation with:")
        print(checkmsg)
        sys.exit(255)

    retrieve_metadata()
    cleanup_deleted()
    cleanup_orphaned()

finally:
    if ssh_connection is not None:
        ssh_connection.terminate()


