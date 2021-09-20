#!/usr/bin/env python3

import sys
import os
import time
import json
import shutil
import argparse
import uuid
import subprocess
import tempfile
import pathlib
from copy import deepcopy

default_prepdir = tempfile.mkdtemp()

ssh_socketfile = '/tmp/remarkable-push.socket'

parser = argparse.ArgumentParser(description='Push files to your reMarkable')
parser.add_argument('-o', '--output', action='store', default=None, dest='output_destination', metavar='<folder>', help='')
parser.add_argument('-r', '--remote-address', action='store', default='10.11.99.1', dest='ssh_destination', metavar='<IP or hostname>', help='remote address of the reMarkable')
parser.add_argument('--transfer-dir', metavar='<directory name>', dest='prepdir', type=str, default=default_prepdir, help='')
parser.add_argument('--dry-run', dest='dryrun', action='store_true', default=False, help="Create the payload, but don't ship it to the reMarkable")
parser.add_argument('-s', '--skip-existing-files', dest='skip_existing_files', action='store_true', default=False, help="Don't copy additional versions of existing files")
parser.add_argument('--overwrite', dest='overwrite', action='store_true', default=False, help="Overwrite existing files with a new version (potentially destructive)")
parser.add_argument('--overwrite_doc_only', dest='overwrite_doc_only', action='store_true', default=False, help="Overwrite the underlying file only, keep notes and such (potentially destructive)")
parser.add_argument('--debug', dest='debug', action='store_true', default=False, help="Render documents, but don't copy to remarkable.")
parser.add_argument('documents', metavar='documents', type=str, nargs='*', help='')

args = parser.parse_args()

if args.overwrite_doc_only:
	args.overwrite = True


ssh_connection = subprocess.Popen(f'ssh -o ConnectTimeout=1 -M -N -q -S {ssh_socketfile} root@{args.ssh_destination}', shell=True)


class FileCollision(Exception):
	pass

class UnexpectedSituation(Exception):
	pass


#########################
#
#   Helper functions
#
#########################


def gen_did():
	"""
	generates a uuid according to necessities (and marks it if desired for debugging and such)
	"""
	did = str(uuid.uuid4())
	did =  'f'*8 + did[8:]  # for debugging purposes
	return did


def validate_filetype(filetype):
	"""
	double-checks that the filetype we submit is one we can actually process, just in case
	"""
	if filetype not in ['folder', 'pdf', 'epub']:
		raise ValueError("Unknown or no filetype provided.")

def construct_metadata(filetype, name, parent_id=''):
	"""
	constructs a metadata-json for a specified document
	"""
	meta={
		"visibleName": name,
		"parent": parent_id,
		"lastModified": int(time.time()*1000),
		#"lastOpenedPage": 0,  # on
		"metadatamodified": False,
		"modified": False,
		"pinned": False,
		"synced": False,
		"type": "CollectionType",
		"version": 0,
		"deleted": False,
	}

	if filetype in ['pdf', 'epub']:
		meta["lastOpenedPage"] = 0     # only for pdfs & epubs
		meta["type"] = "DocumentType"  # changed from default

	return meta


def get_metadata_by_uuid(u):
	"""
	retrieves metadata for a given document identified by its uuid
	"""
	raw_metadata = subprocess.getoutput(f'ssh -S {ssh_socketfile} root@{args.ssh_destination} "cat .local/share/remarkable/xochitl/{u}.metadata"')
	#raw_metadata = subprocess.getoutput(f'ssh -S {ssh_socketfile} root@{ssh_destination} "cat .local/share/remarkable/xochitl/{u}.metadata"')
	try:
		metadata = json.loads(raw_metadata)

		if metadata['deleted'] or metadata['parent'] == 'trash':
			return None
		else:
			return metadata

	except json.decoder.JSONDecodeError:
		return None


def get_metadata_by_visibleName(name):
	"""
	retrieves metadata for all given documents that have the given name set as visibleName
	"""
	#pattern = f'"visibleName": "{name}"'
	cmd = f'ssh -S {ssh_socketfile} root@{args.ssh_destination} "grep -lF \'\\"visibleName\\": \\"{name}\\"\' .local/share/remarkable/xochitl/*.metadata"'
	#cmd = f'ssh -S {ssh_socketfile} root@{ssh_destination} "grep -lF \'\\"visibleName\\": \\"{name}\\"\' .local/share/remarkable/xochitl/*.metadata"'
	res = subprocess.getoutput(cmd)

	reslist = []
	if res != '':
		for result in res.split('\n'):

			# hard pattern matching to provoke a mismatch-exception on the first number mismatch
			_, _, _, _, filename = result.split('/')
			u, _ = filename.split('.')

			metadata = get_metadata_by_uuid(u)
			if metadata:
				reslist.append((u, metadata))

	return reslist



#################################
#
#   Document tree abstraction
#
#################################


class Node:

	def __init__(self, name, parent=None, filetype=None, document=None):
		validate_filetype(filetype)

		self.name = name
		self.filetype = filetype
		self.doctype = 'CollectionType' if filetype == 'folder' else 'DocumentType'
		self.parent = parent
		self.children = []
		if filetype in ['pdf', 'epub']:
			if document is not None:
				self.doc = document
			else:
				raise TypeError("No document provided for file node " + name)

		self.id = None
		self.exists = False
		self.gets_modified = False


	def add_child(self, node):
		"""
		add a child to this Node and make sure its parent is appropriately set
		"""
		node.parent = self
		self.children.append(node)


	def sync_ids(self):
		"""
		walks the document tree we constructed and retrieves the document IDs for everything
		that already exists on the remarkable;
		it also assigns ids based on our desired outcome, i.e. uploading everything, keeping things synced or overwriting
		"""
		metadata = get_metadata_by_visibleName(self.name)

		# first, we filter the metadata we got for those that are actually in the same location
		# in the document tree that this node is, i.e. same parent and same document type
		filtered_metadata = []
		for (did, md) in metadata:
			location_match = (self.parent is None and md['parent'] == '') or (self.parent.id == md['parent'])  # (is root node) or (has matching parent)
			type_match = self.doctype == md['type']
			if location_match and type_match:
				# only keep metadata at the same location in the filesystem tree
				filtered_metadata.append((did, md))

		metadata = filtered_metadata


		if len(metadata) == 0 or (self.doctype == 'DocumentType' and not args.overwrite and not args.skip_existing_files):
			# nonexistent or we don't care about existing documents (latter for files only)
			self.id = gen_did()
			self.exists = False

		elif len(metadata) == 1:

			# ok, we have a document already in place at this node_point that fits the position in the document tree
			# first, get unpack its metadata and assign the document id
			did, md = metadata[0]
			self.id = did

			if self.doctype == 'CollectionType' or not args.overwrite:

				# if it's a folder or if we do not intend to overwrite anything, simply mark the node as existing;
				# if it's a document and we don't overwrite, but also don't want to skip, the first if-branch handles
				# this already, so here we only have to care about overwrites and implicitly skip everything that's
				# already there
				self.exists = True

			else:

				# ok, we want to overwrite a document. We need to pretend it's not there so it gets rendered, so let's
				# lie to our parser here, claiming there is nothing
				self.exists = False
				self.gets_modified = True  # and make a note to properly mark it in case of a dry run
				if args.overwrite_doc_only:
					# if we only want to overwrite the document file itself, but keep everything else,
					# we simply switch out the render function of this node to a simple document copy
					# might mess with xochitl's thumbnail-generation and other things, but overall seems to be fine
					self.render = lambda prepdir: shutil.copy(self.doc, f'{prepdir}/{self.id}.{self.filetype}')

		else:
			# ok, something is still ambiguous
			# at this point in the code we don't want to simply ignore existing files, that'd be the first if-branch,
			# and everything else requires that we can pinpoint a specific file (even skipping requires that we know
			# what we want to skip, if there are two, is it one of those or actually an entirely different file?)
			# hence, we error out here as currently the risk of breaking something is too great at this point
			destination_name = self.parent.name if self.parent is not None else 'toplevel'
			msg = f"File or folder {self.name} occurs multiple times in destination {destination_name}. Situation ambiguous, cannot decide how to proceed."
			print(msg, file=sys.stderr)
			sys.exit(1)


		for ch in self.children:
			ch.sync_ids()


	def render_common(self, prepdir):
		"""
		renders all files that are shared between the different DocumentTypes
		"""

		with open(f'{prepdir}/{self.id}.metadata', 'w') as f:
			if self.parent:
				metadata = construct_metadata(self.filetype, self.name, parent_id=self.parent.id)
			else:
				metadata = construct_metadata(self.filetype, self.name)
			json.dump(metadata, f, indent=4)

		with open(f'{prepdir}/{self.id}.content', 'w') as f:
			json.dump({}, f, indent=4)


	def render(self, prepdir):
		"""
		This renders the given note, including DocumentType specifics;
		needs to be reimplemented by the subclasses
		"""
		raise Exception("Rendering not implemented")



class Document(Node):

	def __init__(self, document, parent=None):

		docpath = pathlib.Path(document)
		filetype = docpath.suffix[1:] if docpath.suffix.startswith('.') else docpath.suffix

		super().__init__(docpath.name, parent=parent, filetype=filetype, document=docpath)


	def render(self, prepdir):
		"""
		renders a DocumentType that is not a folder
		"""
		if not self.exists:

			self.render_common(prepdir)

			os.makedirs(f'{prepdir}/{self.id}')
			os.makedirs(f'{prepdir}/{self.id}.thumbnails')
			shutil.copy(self.doc, f'{prepdir}/{self.id}.{self.filetype}')


class Folder(Node):

	def __init__(self, name, parent=None, document=None):
		super().__init__(name, parent=parent, filetype='folder')


	def render(self, prepdir):
		"""
		renders a folder
		"""
		if not self.exists:

			self.render_common(prepdir)

		for ch in self.children:
			ch.render(prepdir)



############################
#
#   actual syncing logic
#
############################


# first, assemble the given output directory (-o) where everything shall be sorted into
# into our document tree representation

root   = None  # the overall root node
anchor = None  # the anchor to which we append new documents

if args.output_destination:
	folders = args.output_destination.split('/')
	root = anchor = Folder(folders[0])

	for folder in folders[1:]:
		ch = Folder(folder)
		anchor.add_child(ch)
		anchor = ch

def construct_node_tree(basepath, parent=None):
	"""
	this recursively constructs the document tree based on the top-level
	document/folder data structure on disk that we put in initially
	"""
	path = pathlib.Path(basepath)
	if path.is_dir():
		node = Folder(path.name, parent=parent)
		for f in os.listdir(path):
			child = construct_node_tree(path/f, parent=node)
			node.add_child(child)

	elif path.is_file() and path.suffix.lower() in ['.pdf', '.epub']:
		node = Document(path)

	return node

# then add the actual folders/documents to the tree at the anchor point
if anchor is None:
	root = []
	for doc in args.documents:
		root.append(construct_node_tree(doc))

else:
	for doc in args.documents:
		anchor.add_child(construct_node_tree(doc))

	# make it into a 1-element list to streamline code further down
	root = [root]


# now synchronize the document tree
for r in root:
	r.sync_ids()


if args.dryrun:

	try:
		from termcolor import colored
	except ImportError:
		colored = lambda s, c: s

	# just print a filesystem tree for the remarkable representation of what we are going to create
	def print_tree(node, padding):
		"""
		prints a filesystem representation of the constructed document tree,
		including a note if the according node already exists on the remarkable or not
		"""
		if node.gets_modified:
			note = colored("| !!! gets modified !!!", 'red')
		elif node.exists:
			note = colored("| exists already", 'green')
		else:
			note = ""

		print(padding, node.name, note)

		for ch in node.children:
			print_tree(ch, padding+"    ")


	if type(root) == list:
		for r in root:
			print_tree(r, "")
			print()
	else:
		print_tree(root, "")

elif args.debug:

	for r in root:
		r.render(args.prepdir)
	print(f' --> Payload data can be found in {args.prepdir}')

else:

	for r in root:
		r.render(args.prepdir)

	subprocess.call(f'scp -r {args.prepdir}/* root@{args.ssh_destination}:.local/share/remarkable/xochitl', shell=True)
	subprocess.call(f'ssh -S {ssh_socketfile} root@{args.ssh_destination} systemctl restart xochitl', shell=True)

	if args.prepdir == default_prepdir:  # aka we created it
		shutil.rmtree(args.prepdir)

ssh_connection.terminate()
#os.remove(ssh_socketfile)
