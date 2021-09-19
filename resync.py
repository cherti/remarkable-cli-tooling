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
parser.add_argument('documents', metavar='documents', type=str, nargs='*', help='')

args = parser.parse_args()

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


def validate_doctype(doctype):
	"""
	double-checks that the doctype we submit is one we can actually process, just in case
	"""
	if doctype not in ['folder', 'pdf', 'epub']:
		raise ValueError("Unknown or no doctype provided.")

def construct_metadata(doctype, name, parent_id=''):
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

	if doctype in ['pdf', 'epub']:
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

		if metadata['deleted']:
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

	def __init__(self, name, parent=None, doctype=None, document=None):
		validate_doctype(doctype)

		self.name = name
		self.doctype = doctype
		self.parent = parent
		self.children = []
		if doctype in ['pdf', 'epub']:
			if document is not None:
				self.doc = document
			else:
				raise TypeError("No document provided for file node " + name)

		self.id = None
		self.exists = False


	def add_child(self, node):
		"""
		add a child to this Node and make sure its parent is appropriately set
		"""
		node.parent = self
		self.children.append(node)


	def sync_ids(self):
		"""
		walks the document tree we constructed and retrieves the document IDs for everything
		that already exists on the remarkable
		"""
		metadata = get_metadata_by_visibleName(self.name)
		if len(metadata) == 0:
			# nonexistent or we don't care about existing documents (latter for files only)
			self.id = gen_did()
			self.exists = False
		elif len(metadata) == 1:
			did, md = metadata[0]
			if (self.parent is None and md['parent'] == '')	or (self.parent.id == md['parent']):
				# we got the right node
				self.id = did
				self.exists = True
		else:
			# this is a difficult one. We match multiple nodes in the existing tree
			# and we cannot know if those we have shall be new ones or if we should
			# use the existing ones.
			# Therefore we will assume:
			#   * existing folders are reused and not newly created
			#   * existing files will be recreated and not replaced
			#
			# for the second point it might make sense to provide flags at some point
			candidates = 0
			for did, md in metadata:
				if (self.parent is None and md['parent'] == '')	or (self.parent.id == md['parent']):
					# (is root node) or (has matching parent) => we got the right node
					self.id = did
					self.exists = True
					candidates += 1

			if candidates != 1:
				raise UnexpectedSituation(f"File {self.name} has multiple matching parents. That should not be possible?")


		for ch in self.children:
			ch.sync_ids()


	def render_common(self, prepdir):
		"""
		renders all files that are shared between the different DocumentTypes
		"""

		with open(f'{prepdir}/{self.id}.metadata', 'w') as f:
			if self.parent:
				metadata = construct_metadata(self.doctype, self.name, parent_id=self.parent.id)
			else:
				metadata = construct_metadata(self.doctype, self.name)
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
		doctype = docpath.suffix[1:] if docpath.suffix.startswith('.') else docpath.suffix

		super().__init__(docpath.name, parent=parent, doctype=doctype, document=docpath)


	def render(self, prepdir):
		"""
		renders a DocumentType that is not a folder
		"""
		print("rendering", self.name)
		if not self.exists:

			self.render_common(prepdir)

			os.makedirs(f'{prepdir}/{self.id}')
			os.makedirs(f'{prepdir}/{self.id}.thumbnails')
			shutil.copy(self.doc, f'{prepdir}/{self.id}.{self.doctype}')


class Folder(Node):

	def __init__(self, name, parent=None, document=None):
		super().__init__(name, parent=parent, doctype='folder')


	def render(self, prepdir):
		"""
		renders a folder
		"""
		print("rendering", self.name)
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

	for r in root:
		r.sync_ids()

	for r in root:
		r.render(args.prepdir)

else:
	for doc in args.documents:
		anchor.add_child(construct_node_tree(doc))

	# we'll only ever have one root if we specified an output dir
	root.sync_ids()
	root.render(args.prepdir)

if args.dryrun:

	# just print a filesystem tree for the remarkable representation of what we are going to create
	def print_tree(node, padding):
		"""
		prints a filesystem representation of the constructed document tree,
		including a note if the according node already exists on the remarkable or not
		"""
		print(padding, node.name, "| exists already:", node.exists)
		for ch in node.children:
			print_tree(ch, padding+"    ")

	if type(root) == list:
		for r in root:
			print_tree(r, "")
			print()
	else:
		print_tree(root, "")

	print(f' --> Payload data can be found in {args.prepdir}')
else:
	subprocess.call(f'scp -r {args.prepdir}/* root@{args.ssh_destination}:.local/share/remarkable/xochitl', shell=True)
	subprocess.call(f'ssh -S {ssh_socketfile} root@{args.ssh_destination} systemctl restart xochitl', shell=True)

	if args.prepdir == default_prepdir:  # aka we created it
		shutil.rmtree(args.prepdir)

ssh_connection.terminate()
#os.remove(ssh_socketfile)
