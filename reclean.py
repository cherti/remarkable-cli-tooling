#!/usr/bin/env python3

import sys
import json
import argparse
import subprocess
import tempfile
import pathlib
import tqdm

default_prepdir = tempfile.mkdtemp()

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

args = parser.parse_args()

ssh_command = f'ssh -o PubkeyAcceptedKeyTypes=+ssh-rsa -o HostKeyAlgorithms=+ssh-rsa -S {ssh_socketfile} root@{args.ssh_destination}'

def ssh(arg,dry=False):
    if dry:
        print(f'{ssh_command} {arg}')
    else:
        return subprocess.getoutput(f'{ssh_command} {arg}')


def get_metadata_by_uuid(u):
    """
    retrieves metadata for a given document identified by its uuid
    """
    raw_metadata = ssh(f'"cat ~/.local/share/remarkable/xochitl/{u}.metadata"')
    try:
        metadata = json.loads(raw_metadata)
        return metadata

    except json.decoder.JSONDecodeError:
        return None


#################################
#
#   Clean up deleted files
#
#################################

def cleanup_deleted():

    document_metadata = ssh(f'"ls -1 ~/.local/share/remarkable/xochitl/*.metadata"')
    metadata_uuids = set([pathlib.Path(d).stem for d in document_metadata.split('\n')])

    deleted_uuids = []
    limit = 10
    for u in tqdm.tqdm(metadata_uuids):
        if get_metadata_by_uuid(u)['deleted']:
            deleted_uuids.append(u)

    print(f'checking for deleted files - {limit}% done')

    if len(deleted_uuids) == 0:
        print('No deleted files found.')
    else:
        decision = input(f'Clean up {len(deleted_uuids)} deleted files? [Y/n]')
        if decision in ['', 'y', 'Y']:
            for u in deleted_uuids:
                ssh("rm -r ~/.local/share/remarkable/xochitl/{u}*", dry=args.dryrun)

    return metadata_uuids


#################################
#
#   Clean up orphaned files
#
#################################

def cleanup_orphaned(metadata_uuids):
    all_document_ls = ssh(f'"ls -1 ~/.local/share/remarkable/xochitl"')
    all_document_files = [pathlib.Path(p) for p in all_document_ls.split('\n')]
    all_uuids = set([d.stem for d in all_document_files])

    orphaned_file_stems = all_uuids.difference(metadata_uuids)

    if len(orphaned_file_stems) > 0:
        decision = input(f"Clear {len(orphaned_file_stems)} orphaned files that don't have metadata associated with them? [Y/n]")
    else:
        decision = 'n'

    if decision in ['', 'y', 'Y']:

        # now let's make sure all our orphaned nodes are actually unambiguous
        orphan_deletion_candidates = []
        for ofs in orphaned_file_stems:
            # get all files that would match our ofs* deletion pattern later, and their stems
            deletion_candidates = [d for d in all_document_files if d.name.startswith(ofs)]
            orphan_stems = set([d.stem for d in deletion_candidates])

            # if we didn't mismatch anything, i.e. having a file with stem 'a' and matching all uuids 'a*',
            # we only have one orphan stem in the set. Otherwise we have an ambiguity and leave fixing to the user.
            if len(orphan_stems) > 1:
                print(f'~/.local/share/remarkable/xochitl/{ofs}* has no metadata, but matches more than one document or file. Ignoring this, you will have to check this manually.')
            else:
                orphan_deletion_candidates.append(ofs)


        for of in orphan_deletion_candidates:
            ssh(f'\'rm "/home/root/.local/share/remarkable/xochitl/{of}"*\'', dry=args.dryrun)



ssh_connection = None
try:
    ssh_connection = subprocess.Popen(f'{ssh_command} -o ConnectTimeout=1 -M -N -q ', shell=True)

    # quickly check if we actually have a functional ssh connection (might not be the case right after an update)
    checkmsg = ssh("/bin/true")
    if checkmsg != "":
        print("ssh connection does not work, verify that you can manually ssh into your reMarkable. ssh itself commented the situation with:")
        print(checkmsg)
        sys.exit(255)

    metadata_uuids = cleanup_deleted()
    cleanup_orphaned(metadata_uuids)

finally:
    if ssh_connection is not None:
        ssh_connection.terminate()


