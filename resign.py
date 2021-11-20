#!/usr/bin/env python3

# set this to your resync-executable (if it's in path, its name should suffice)
resync_cmd = 'resync.py'


import sys, os, subprocess, pathlib, tempfile, shutil, argparse, json

parser = argparse.ArgumentParser(description='Relay documents over your reMarkable for signing')
parser.add_argument('-r', '--remote-address', action='store', default='10.11.99.1', dest='ssh_destination', metavar='<IP or hostname>', help='remote address of the reMarkable')
parser.add_argument('documents', metavar='documents', type=str, nargs='*', help='Documents and folders to be signed')
args = parser.parse_args()


prepdir = pathlib.Path(tempfile.mkdtemp())
ssh_socketfile = '/tmp/remarkable-push.socket'
ssh_connection = subprocess.Popen(f'ssh -o ConnectTimeout=1 -M -N -q -S {ssh_socketfile} root@{args.ssh_destination}', shell=True)

docs = [pathlib.Path(p) for p in args.documents]

targetfiles = []
for doc in docs:
	destname = 'sign_' + doc.name
	shutil.copy(doc, prepdir/destname)
	targetfiles.append(destname)

pushdocs = [prepdir/doc for doc in os.listdir(prepdir)]

try:
	subprocess.call([resync_cmd, '--remote-address', args.ssh_destination, 'push'] + pushdocs)
except FileNotFoundError:
	print("Could not locate {resync_cmd}, maybe it's not in your path?", file=sys.stderr)
	sys.exit(1)

for doc in pushdocs:
	os.remove(doc)

input("Now sign all documents and press enter once you're done.")

subprocess.call([resync_cmd, '--remote-address', args.ssh_destination, '-o', prepdir, 'pull'] + targetfiles)
for f in targetfiles:
	destname = os.path.splitext(f[5:])[0] + '_signed.pdf'
	shutil.move(prepdir/f, destname)

shutil.rmtree(prepdir)

#######################################################################
#
# now let's clean up after ourselves on the remarkable
# this is a bit more elaborate as we need to fumble on the remarkable
#
#######################################################################


def get_uuid_by_visibleName(name):
	"""
	retrieves uuid for all given documents that have the given name set as visibleName
	"""
	#pattern = f'"visibleName": "{name}"'
	cmd = f'ssh -S {ssh_socketfile} root@{args.ssh_destination} "grep -lF \'\\"visibleName\\": \\"{name}\\"\' .local/share/remarkable/xochitl/*.metadata"'
	res = subprocess.getoutput(cmd)

	uuid_candidates = []
	if res != '':
		for result in res.split('\n'):
			try:
				# hard pattern matching to provoke a mismatch-exception on the first number mismatch
				_, _, _, _, filename = result.split('/')
			except ValueError:
				continue

			u, _ = filename.split('.')

			raw_metadata = subprocess.getoutput(f'ssh -S {ssh_socketfile} root@{args.ssh_destination} "cat .local/share/remarkable/xochitl/{u}.metadata"')

			try:
				metadata = json.loads(raw_metadata)
			except json.decoder.JSONDecodeError:
				continue

			if metadata is not None and metadata['parent'] == '':
				uuid_candidates.append(u)

	if len(uuid_candidates) > 1:
		print("Document {name} was found multiple times, not cleaning it up, delete manually")
	elif len(uuid_candidates) < 1:
		print("Document {name} was not found, unable to clean it up")
	else:
		return uuid_candidates[0]

	return None


for tf in targetfiles:
	u = get_uuid_by_visibleName(tf)
	if u is not None:
		cmd = f'ssh -S {ssh_socketfile} root@{args.ssh_destination} "rm -r ~/.local/share/remarkable/xochitl/{u}*"'
		subprocess.call(cmd, shell=True)

subprocess.call(f'ssh -S {ssh_socketfile} root@{args.ssh_destination} systemctl restart xochitl', shell=True)
ssh_connection.terminate()
print("All documents processed, have fun with your remaining paperwork. :)")
