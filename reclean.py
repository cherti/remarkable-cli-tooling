#!/usr/bin/env python3

import json
import argparse
import subprocess
import tempfile
import pathlib

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


ssh_connection = subprocess.Popen(f'ssh -o ConnectTimeout=1 -M -N -q -S {ssh_socketfile} root@{args.ssh_destination}', shell=True)

# quickly check if we actually have a functional ssh connection (might not be the case right after an update)
checkmsg = subprocess.getoutput(f'ssh -S {ssh_socketfile} root@{args.ssh_destination} "/bin/true"')
if checkmsg != "":
	print("ssh connection does not work, verify that you can manually ssh into your reMarkable. ssh itself commented the situation with:")
	print(checkmsg)
	ssh_connection.terminate()
	sys.exit(255)


def get_metadata_by_uuid(u):
	"""
	retrieves metadata for a given document identified by its uuid
	"""
	raw_metadata = subprocess.getoutput(f'ssh -S {ssh_socketfile} root@{args.ssh_destination} "cat ~/.local/share/remarkable/xochitl/{u}.metadata"')
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

document_metadata = subprocess.getoutput(f'ssh -S {ssh_socketfile} root@{args.ssh_destination} "ls -1 ~/.local/share/remarkable/xochitl/*.metadata"')
metadata_uuids = set([pathlib.Path(d).stem for d in document_metadata.split('\n')])

deleted_uuids = []
limit = 10
for i, u in enumerate(metadata_uuids):
	if i/len(metadata_uuids)*100 > limit:
		print(f'checking for deleted files - {limit}% done')
		limit += 10


	if get_metadata_by_uuid(u)['deleted']:
		deleted_uuids.append(u)

print(f'checking for deleted files - {limit}% done')

if len(deleted_uuids) == 0:
	print('No deleted files found.')
else:

	decision = input(f'Clean up {len(deleted_uuids)} deleted files? [Y/n]')
	if decision in ['', 'y', 'Y']:
		for u in deleted_uuids:
			cmd = f'ssh -S {ssh_socketfile} root@{args.ssh_destination} "rm -r ~/.local/share/remarkable/xochitl/{u}*"'
			if args.dryrun:
				print(cmd)
			else:
				subprocess.getoutput(cmd)



#################################
#
#   Clean up orphaned files
#
#################################

all_document_ls = subprocess.getoutput(f'ssh -S {ssh_socketfile} root@{args.ssh_destination} "ls -1 ~/.local/share/remarkable/xochitl"')
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
		cmd = f'ssh -S {ssh_socketfile} root@{args.ssh_destination} \'rm "/home/root/.local/share/remarkable/xochitl/{of}"*\''
		if args.dryrun:
			print(cmd)
		else:
			subprocess.call(cmd, shell=True)


ssh_connection.terminate()
