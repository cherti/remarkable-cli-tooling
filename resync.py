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
import urllib.request
from copy import deepcopy

default_prepdir = tempfile.mkdtemp()

ssh_socketfile = '/tmp/remarkable-push.socket'

parser = argparse.ArgumentParser(description='Push and pull files to and from your reMarkable')

parser.add_argument('--dry-run', dest='dryrun', action='store_true', default=False, help="Don't actually copy files, just show what would be copied (currently push only)")
parser.add_argument('-o', '--output', action='store', default=None, dest='output_destination', metavar='<folder>', help='Destination for copied files, either on or off device')

existing_files_handling = parser.add_mutually_exclusive_group()
existing_files_handling.add_argument('-s', '--skip-existing-files', dest='skip_existing_files', action='store_true', default=False, help="Don't copy additional versions of existing files")
existing_files_handling.add_argument('--overwrite', dest='overwrite', action='store_true', default=False, help="Overwrite existing files with a new version (potentially destructive)")
existing_files_handling.add_argument('--overwrite_doc_only', dest='overwrite_doc_only', action='store_true', default=False, help="Overwrite the underlying file only, keep notes and such (potentially destructive)")

parser.add_argument('-r', '--remote-address', action='store', default='10.11.99.1', dest='ssh_destination', metavar='<IP or hostname>', help='remote address of the reMarkable')
parser.add_argument('--transfer-dir', metavar='<directory name>', dest='prepdir', type=str, default=default_prepdir, help='custom directory to render files to-be-upload')
parser.add_argument('--debug', dest='debug', action='store_true', default=False, help="Render documents, but don't copy to remarkable.")

parser.add_argument('mode', metavar='mode', type=str, help='push or pull')
parser.add_argument('documents', metavar='documents', type=str, nargs='*', help='Documents and folders to be pushed to the reMarkable')

args = parser.parse_args()

if args.overwrite_doc_only:
	args.overwrite = True


ssh_connection = subprocess.Popen(f'ssh -o ConnectTimeout=1 -M -N -q -S {ssh_socketfile} root@{args.ssh_destination}', shell=True)


class FileCollision(Exception):
	pass

class ShouldNeverHappenError(Exception):
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


def construct_metadata(filetype, name, parent_id=''):
	"""
	constructs a metadata-json for a specified document
	"""
	meta={
		"visibleName": name,
		"parent": parent_id,
		"lastModified": int(time.time()*1000),
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

		# now retrieve the document ID for this document if it already exists
		metadata = get_metadata_by_visibleName(self.name)

		# first, we filter the metadata we got for those that are actually in the same location
		# in the document tree that this node is, i.e. same parent and same document type
		filtered_metadata = []
		for (did, md) in metadata:

			# ˇ (is root node) or (has matching parent) ˇ
			location_match = (self.parent is None and md['parent'] == '') or (self.parent is not None and self.parent.id == md['parent'])
			type_match = self.doctype == md['type']
			if location_match and type_match:
				# only keep metadata at the same location in the filesystem tree
				filtered_metadata.append((did, md))


		if len(filtered_metadata) == 1:

			# ok, we have a document already in place at this node_point that fits the position in the document tree
			# first, get unpack its metadata and assign the document id
			did, md = metadata[0]
			self.id = did
			self.exists = True

		elif len(filtered_metadata) > 1 and (args.skip_existing_files or args.overwrite) and args.mode == 'push':
			# ok, something is still ambiguous, but for what we want to do we cannot have that.
			# Hence, we error out here as currently the risk of breaking something is too great at this point.
			destination_name = self.parent.name if self.parent is not None else 'toplevel'
			msg = f"File or folder {self.name} occurs multiple times in destination {destination_name}. Situation ambiguous, cannot decide how to proceed."
			print(msg, file=sys.stderr)
			sys.exit(1)


	def __repr__(self):
		return self.get_full_path()


	def add_child(self, node):
		"""
		add a child to this Node and make sure it has a parent set
		"""
		if node.parent is None:
			raise ShouldNeverHappenError("Child was added without having a parent set.")

		self.children.append(node)


	def get_full_path(self):
		if self.parent is None:
			return self.name
		else:
			return self.parent.get_full_path() + '/' + self.name


	def render_common(self, prepdir):
		"""
		renders all files that are shared between the different DocumentTypes
		"""

		if self.id is None:
			self.id = gen_did()

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


	def build_downwards(self):
		"""
		This creates a document tree for all nodes that are direct and indirect
		descendants of this node.
		"""
		if self.filetype != 'folder':
			# documents don't have children, this one's easy
			return

		cmd = f'ssh -S {ssh_socketfile} root@{args.ssh_destination} "grep -lF \'\\"parent\\": \\"{self.id}\\"\' .local/share/remarkable/xochitl/*.metadata"'
		children_uuids = set([pathlib.Path(d).stem for d in subprocess.getoutput(cmd).split('\n')])

		for chu in children_uuids:
			md = get_metadata_by_uuid(chu)
			if md['type'] == "CollectionType":
				ch = Folder(md['visibleName'], parent=self)
			else:

				name = md['visibleName']

				if not name.endswith('.pdf'):
					name += '.pdf'

				ch = Document(name, parent=self)

			ch.id = chu
			self.add_child(ch)
			ch.build_downwards()


	def download(self, targetdir=pathlib.Path.cwd()):
		"""
		retrieve document node from the remarkable to local system
		"""
		os.chdir(targetdir)
		if self.filetype == 'folder':
			# folders we simply create ourselves
			os.makedirs(self.name, exist_ok=True)
			for ch in self.children:
				ch.download(targetdir/self.name)
		else:
			# documents we need to actually download
			resp = urllib.request.urlopen(f'http://{args.ssh_destination}/download/{self.id}/placeholder')
			filename = self.name if self.name.lower().endswith('.pdf') else f'{self.name}.pdf'
			with open(filename, 'wb') as f:
				f.write(resp.read())


class Document(Node):

	def __init__(self, document, parent=None):

		docpath = pathlib.Path(document)
		filetype = docpath.suffix[1:] if docpath.suffix.startswith('.') else docpath.suffix

		super().__init__(docpath.name, parent=parent, filetype=filetype, document=docpath)


	def render(self, prepdir):
		"""
		renders an actual DocumentType tree node
		"""
		if not self.exists:

			self.render_common(prepdir)

			os.makedirs(f'{prepdir}/{self.id}')
			os.makedirs(f'{prepdir}/{self.id}.thumbnails')
			shutil.copy(self.doc, f'{prepdir}/{self.id}.{self.filetype}')


class Folder(Node):

	def __init__(self, name, parent=None):
		super().__init__(name, parent=parent, filetype='folder')


	def render(self, prepdir):
		"""
		renders a folder tree node
		"""
		if not self.exists:
			self.render_common(prepdir)

		for ch in self.children:
			ch.render(prepdir)


def identify_node(name, parent=None):
	"""
	infer a node's type by name and location, and return a node object
	in case this is unambiguously possible
	"""
	metadata = get_metadata_by_visibleName(name)
	candidates = []

	for u, md in metadata:
		location_match = (parent is None and md['parent'] == '') or (parent.id == md['parent'])  # (is root node) or (has matching parent)
		if location_match:
			candidates.append((u, md))

		if len(candidates) == 1:
			u, md = candidates[0]
			return Document(name, parent=parent) if md['type'] == 'DocumentType' else Folder(name, parent=parent)
		else:
			return None



###############################
#
#   actual application logic
#
###############################


def push_to_remarkable(documents, destination=None, overwrite=False, skip_existing=False):
	"""
	push a list of documents to the reMarkable

	documents: list of documents
	destination: location on the device
	"""

	def construct_node_tree_from_disk(basepath, parent=None):
		"""
		this recursively constructs the document tree based on the top-level
		document/folder data structure on disk that we put in initially
		"""
		path = pathlib.Path(basepath)
		if path.is_dir():
			node = Folder(path.name, parent=parent)
			for f in os.listdir(path):
				child = construct_node_tree_from_disk(path/f, parent=node)
				node.add_child(child)

		elif path.is_file() and path.suffix.lower() in ['.pdf', '.epub']:
			node = Document(path, parent=parent)
			if node.exists:
				if not skip_existing and not overwrite:
					# if we don't skip existing files, this file gets a new document ID
					# and becomes a new file next to the existing one
					node.id = gen_did()
					node.exists = False
				elif overwrite:
					# ok, we want to overwrite a document. We need to pretend it's not there so it gets rendered, so let's
					# lie to our parser here, claiming there is nothing
					node.exists = False
					node.gets_modified = True  # and make a note to properly mark it in case of a dry run
					if args.overwrite_doc_only:
						# if we only want to overwrite the document file itself, but keep everything else,
						# we simply switch out the render function of this node to a simple document copy
						# might mess with xochitl's thumbnail-generation and other things, but overall seems to be fine
						node.render = lambda self, prepdir: shutil.copy(self.doc, f'{prepdir}/{self.id}.{self.filetype}')

		return node


	# first, assemble the given output directory (-o) where everything shall be sorted into
	# into our document tree representation

	root   = None  # the overall root node
	anchor = None  # the anchor to which we append new documents

	if destination:
		folders = destination.split('/')
		root = anchor = Folder(folders[0])

		for folder in folders[1:]:
			ch = Folder(folder, parent=anchor)
			anchor.add_child(ch)
			anchor = ch


	# then add the actual folders/documents to the tree at the anchor point
	if anchor is None:
		root = []
		for doc in documents:
			root.append(construct_node_tree_from_disk(doc))

	else:
		for doc in documents:
			anchor.add_child(construct_node_tree_from_disk(doc, parent=anchor))

		# make it into a 1-element list to streamline code further down
		root = [root]




	if args.dryrun:
		# just print out the assembled document tree with appropriate actions

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

		for r in root:
			print_tree(r, "")
			print()

	elif args.debug:

		for r in root:
			r.render(args.prepdir)
		print(f' --> Payload data can be found in {args.prepdir}')

	else:  # actually upload to the reMarkable

		for r in root:
			r.render(args.prepdir)

		subprocess.call(f'scp -r {args.prepdir}/* root@{args.ssh_destination}:.local/share/remarkable/xochitl', shell=True)
		subprocess.call(f'ssh -S {ssh_socketfile} root@{args.ssh_destination} systemctl restart xochitl', shell=True)

		if args.prepdir == default_prepdir:  # aka we created it
			shutil.rmtree(args.prepdir)


def pull_from_remarkable(documents, destination=None):
	"""
	pull documents from the remarkable to the local system

	documents: list of document paths on the remarkable to pull from
	"""
	destination_directory = pathlib.Path(destination).absolute() if destination is not None else pathlib.Path.cwd()

	anchors = []
	for doc in documents:
		*parents, target = doc.split('/')
		local_anchor = None
		if parents:
			local_anchor = Folder(parents[0], parent=None)
			for par in parents[1:]:

				new_node = Folder(par, parent=local_anchor)
				local_anchor.add_child(new_node)
				local_anchor = new_node

		new_node = identify_node(target, parent=local_anchor)
		if new_node is not None:
			anchors.append(new_node)
		else:
			print(f"Cannot find {doc}, skipping")

	for a in anchors:
		a.build_downwards()
		a.download(targetdir=destination_directory)


if args.mode == 'push':
	push_to_remarkable(args.documents, destination=args.output_destination, overwrite=args.overwrite, skip_existing=args.skip_existing_files)
elif args.mode == 'pull':
	pull_from_remarkable(args.documents, destination=args.output_destination)
else:
	print("Unknown mode, doing nothing.")


ssh_connection.terminate()
#os.remove(ssh_socketfile)
