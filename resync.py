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
import re
import io
import tqdm
from copy import deepcopy

default_prepdir = tempfile.mkdtemp()

ssh_socketfile = '/tmp/remarkable-push.socket'

parser = argparse.ArgumentParser(description='Push and pull files to and from your reMarkable')

parser.add_argument('-n', '--dry-run', dest='dryrun', action='store_true', default=False,
                    help="Don't actually copy files, just show what would be copied")
parser.add_argument('-o', '--output', action='store', default=None, dest='destination', metavar='<folder>',
                    help=('Destination for copied files.'
                          '\nIn the push mode, it specifies a folder on the device.'
                          '\nIn the pull mode, it specifies a local directory.'))
parser.add_argument('-v', dest='verbosity', action='count', default=0,
                    help='verbosity level')


parser.add_argument('--if-exists',
                    choices=["duplicate","overwrite","skip","doconly"],
                    default="skip",
                    help=("Specify the behavior when the destination file exists."
                          "duplicate: Create a duplicate file in the same directory."
                          "overwrite: Overwrite existing files and the metadata."
                          "doconly:   Overwrite existing files but not the metadata."
                          "skip:      Skip the file."))


# parser.add_argument('--if-does-not-exist',
#                     choices=["delete","skip"],
#                     help=("Specify the behavior when the source file does not exist."
#                           "delete: discard the target file."
#                           "skip:      Skip the file. (default when pull)"))


parser.add_argument('-e', '--exclude', dest='exclude_patterns', action='append', default=[],
                    help='exclude a pattern from transfer (must be Python-regex)')

parser.add_argument('-r', '--remote-address', action='store', default='10.11.99.1', dest='ssh_destination', metavar='<IP or hostname>',
                    help='remote address of the reMarkable')
parser.add_argument('--transfer-dir', metavar='<directory name>', dest='prepdir', type=str, default=default_prepdir,
                    help='custom directory to render files to-be-upload')
parser.add_argument('--debug', dest='debug', action='store_true', default=False,
                    help="Render documents, but don't copy to remarkable.")

parser.add_argument('mode', type=str, choices=["push","pull","backup","+","-"],
                    help='push/+, pull/- or backup')
parser.add_argument('documents', metavar='documents', type=str, nargs='*',
                    help='Documents and folders to be pushed to the reMarkable')

args = parser.parse_args()

if args.mode == '+':
    args.mode = 'push'
elif args.mode == '-':
    args.mode = 'pull'


ssh_command = f'ssh -o PubkeyAcceptedKeyTypes=+ssh-rsa -o HostKeyAlgorithms=+ssh-rsa -S {ssh_socketfile} root@{args.ssh_destination}'

def ssh(arg,dry=False):
    if args.verbosity >= 1:
        print(f'{ssh_command} {arg}')
    if not dry:
        return subprocess.getoutput(f'{ssh_command} {arg}')


class FileCollision(Exception):
    pass

class ShouldNeverHappenError(Exception):
    pass


#########################
#
#   Helper functions
#
#########################

def logmsg(lvl, msg):
    if lvl <= args.verbosity:
        print(msg)


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
        "lastModified": str(int(time.time()*1000)),
        "metadatamodified": False,
        "modified": False,
        "pinned": False,
        "synced": False,
        "type": "CollectionType",
        "version": 0,
        "deleted": False,
    }

    if filetype in ['pdf', 'epub']:
        # changed from default
        meta["type"] = "DocumentType"

        # only for pdfs & epubs
        meta["lastOpened"] = meta["lastModified"]
        meta["lastOpenedPage"] = 0

    return meta


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


def curb_tree(node, excludelist):
    """
    removes nodes from a tree based on a list of exclude patterns;
    returns True if the root node is removed, None otherwise as the
    tree is curbed inplace
    """
    for exc in excludelist:
        if re.match(exc, node.get_full_path()) is not None:
            logmsg(2, "curbing "+node.get_full_path())
            return True

    uncurbed_children = []
    for ch in node.children:
        if not curb_tree(ch, excludelist):
            uncurbed_children.append(ch)

    node.children = uncurbed_children

    return False


#################################
#
#   Document tree abstraction
#
#################################


class Node:

    def __init__(self, name, parent=None):

        self.name = name
        self.parent = parent
        self.children = []

        self.gets_modified = False

        # now retrieve the document ID for this document if it already exists
        if parent is None:
            metadata = get_metadata_by_name_and_parent(self.name, "")
        else:
            metadata = get_metadata_by_name_and_parent(self.name, parent.id)

        if metadata:
            uuid, metadata = metadata
            self.id = uuid
            self.exists = True
        else:
            self.id = None
            self.exists = False


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

        logmsg(1, "preparing for upload: " + self.get_full_path())

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


    def build(self):
        """
        This creates a document tree for all nodes that are direct and indirect
        descendants of this node.
        """
        raise Exception("Not implemented")


    def download(self, targetdir=pathlib.Path.cwd()):
        """
        retrieve document node from the remarkable to local system
        """
        raise Exception("Not implemented")


class Document(Node):

    def __init__(self, document, parent=None):

        self.doc = pathlib.Path(document)
        self.doctype = 'DocumentType'
        self.filetype = self.doc.suffix[1:] if self.doc.suffix.startswith('.') else self.doc.suffix

        super().__init__(self.doc.name, parent=parent)


    def render(self, prepdir):
        """
        renders an actual DocumentType tree node
        """
        if not self.exists:

            self.render_common(prepdir)

            os.makedirs(f'{prepdir}/{self.id}')
            os.makedirs(f'{prepdir}/{self.id}.thumbnails')
            shutil.copy(self.doc, f'{prepdir}/{self.id}.{self.filetype}')


    def build(self):
        """
        This creates a document tree for all nodes that are direct and indirect
        descendants of this node.
        """
        # documents don't have children, this one's easy
        return


    def download(self, targetdir=pathlib.Path.cwd()):
        """
        retrieve document node from the remarkable to local system
        """
        if args.dryrun:
            print("downloading document to", targetdir/self.name)
        else:

            logmsg(1, "retrieving " + self.get_full_path())
            os.chdir(targetdir)

            # documents we need to actually download
            filename = self.name if self.name.lower().endswith('.pdf') else f'{self.name}.pdf'
            if os.path.exists(filename):
                if if_exists == "skip":
                    logmsg(0, f"File {filename} already exists, skipping")
                elif if_exists == "overwrite":
                    try:
                        resp = urllib.request.urlopen(f'http://{args.ssh_destination}/download/{self.id}/placeholder')
                        with open(filename, 'wb') as f:
                            f.write(resp.read())
                    except urllib.error.URLError as e:
                        print(f"{e.reason}: Is the web interface enabled? (Settings > Storage > USB web interface)")
                        sys.exit(2)
                else:
                    raise Exception("huh?")
        pass


class Folder(Node):

    def __init__(self, name, parent=None):
        self.doctype  = 'CollectionType'
        self.filetype = 'folder'
        super().__init__(name, parent=parent)


    def render(self, prepdir):
        """
        renders a folder tree node
        """
        if not self.exists:
            self.render_common(prepdir)

        for ch in self.children:
            ch.render(prepdir)


    def build(self):
        """
        This creates a document tree for all nodes that are direct and indirect
        descendants of this node.
        """
        for uuid, metadata in get_metadata_by_parent(self.id).items():
            if metadata['type'] == "CollectionType":
                ch = Folder(metadata['visibleName'], parent=self)
            else:
                name = metadata['visibleName']
                if not name.endswith('.pdf'):
                    name += '.pdf'
                ch = Document(name, parent=self)

            ch.id = uuid
            self.add_child(ch)
            ch.build()


    def download(self, targetdir=pathlib.Path.cwd()):
        """
        retrieve document node from the remarkable to local system
        """
        if args.dryrun:
            # folders we simply create ourselves
            print("creating directory", targetdir/self.name)
            for ch in self.children:
                ch.download(targetdir/self.name)
        else:

            logmsg(1, "retrieving " + self.get_full_path())
            os.chdir(targetdir)

            # folders we simply create ourselves
            os.makedirs(self.name, exist_ok=True)

            for ch in self.children:
                ch.download(targetdir/self.name)


def get_toplevel_files():
    """
    get a list of all documents in the toplevel My files drawer
    """
    toplevel_files = []
    for u, md in get_metadata_by_parent(""):
        toplevel_files.append(md['visibleName'])
    return toplevel_files



###############################
#
#   actual application logic
#
###############################


def push_to_remarkable(documents, destination=None, if_exists="skip", **kwargs):
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
                if child is not None:
                    node.add_child(child)
            return node

        elif path.is_file() and path.suffix.lower() in ['.pdf', '.epub']:
            node = Document(path, parent=parent)
            if node.exists:
                if if_exists == "skip":
                    pass
                elif if_exists == "overwrite":
                    # ok, we want to overwrite a document. We need to pretend it's not there so it gets rendered, so let's
                    # lie to our parser here, claiming there is nothing
                    node.exists = False
                    node.gets_modified = True  # and make a note to properly mark it in case of a dry run

                elif if_exists == "doconly":
                    # ok, we want to overwrite a document. We need to pretend it's not there so it gets rendered, so let's
                    # lie to our parser here, claiming there is nothing
                    node.exists = False
                    node.gets_modified = True  # and make a note to properly mark it in case of a dry run
                    # if we only want to overwrite the document file itself, but keep everything else,
                    # we simply switch out the render function of this node to a simple document copy
                    # might mess with xochitl's thumbnail-generation and other things, but overall seems to be fine
                    node.render = lambda self, prepdir: shutil.copy(self.doc, f'{prepdir}/{self.id}.{self.filetype}')

                elif if_exists == "duplicate":
                    # if we don't skip existing files, this file gets a new document ID
                    # and becomes a new file next to the existing one
                    node.id = gen_did()
                    node.exists = False
                else:
                    raise Exception("huh?")

            return node
        else:
            print(f"unsupported file type, ignored: {path}")
            return None


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
            node = construct_node_tree_from_disk(doc)
            if node is not None:
                root.append(node)

    else:
        for doc in documents:
            node = construct_node_tree_from_disk(doc, parent=anchor)
            if node is not None:
                anchor.add_child(node)

        # make it into a 1-element list to streamline code further down
        root = [root]

    # apply excludes
    curbed_roots = []
    for r in root:
        if not curb_tree(r, args.exclude_patterns):
            curbed_roots.append(r)

    root = curbed_roots


    if args.dryrun:

        # just print out the assembled document tree with appropriate actions

        try:
            from termcolor import colored
        except ImportError:
            colored = lambda s, c: s


        size = shutil.get_terminal_size()
        columns = size.columns
        lines   = size.lines

        # just print a filesystem tree for the remarkable representation of what we are going to create
        def print_tree(node, padding):
            """
            prints a filesystem representation of the constructed document tree,
            including a note if the according node already exists on the remarkable or not
            """
            if node.gets_modified:
                note = " | !!! gets modified !!!"
                notelen = len(note)
                note = colored(note, 'red')
            elif node.exists:
                note = " | exists already"
                notelen = len(note)
                note = colored(note, 'green')
            else:
                note = " | upload"
                notelen = len(note)

            line = padding + node.name
            if len(line) > columns-notelen:
                line = line[:columns-notelen-3] + "..."
            line = line.ljust(columns-notelen)
            print(line+note)

            for ch in node.children:
                print_tree(ch, padding+"  ")

        for r in root:
            print_tree(r, "")
            print()

    elif args.debug:

        for r in root:
            r.render(args.prepdir)
        print(f' --> Payload data can be found in {args.prepdir}')

    else:  # actually upload to the reMarkable

        try:
            for r in root:
                r.render(args.prepdir)

            for f in tqdm.tqdm(os.listdir(args.prepdir)):
                subprocess.call(f'scp -o PubkeyAcceptedKeyTypes=+ssh-rsa -o HostKeyAlgorithms=+ssh-rsa -q {args.prepdir}/{f} root@{args.ssh_destination}:.local/share/remarkable/xochitl/{f}', shell=True)
            ssh(f'systemctl restart xochitl')

        finally:
            if args.prepdir == default_prepdir:  # aka we created it
                shutil.rmtree(args.prepdir)


def pull_from_remarkable(documents, destination=None, if_exists="skip", **kwargs):
    """
    pull documents from the remarkable to the local system

    documents: list of document paths on the remarkable to pull from
    """

    assert if_exists in ["skip", "overwrite"]

    destination_directory = pathlib.Path(destination).absolute() if destination is not None else pathlib.Path.cwd()
    if not destination_directory.exists():
        print("Output directory non-existing, exiting.", file=sys.stderr)

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

        metadata = get_metadata_by_name(target)
        if metadata is not None:
            if metadata['type'] == 'DocumentType':
                new_node = Document(target, parent=local_anchor)
            else:
                new_node = Folder(target, parent=local_anchor)
            anchors.append(new_node)
        else:
            print(f"Cannot find {doc}, skipping")


    for a in anchors:
        a.build()
        if not curb_tree(a, args.exclude_patterns):
            a.download(targetdir=destination_directory)



ssh_connection = None
try:
    ssh_connection = subprocess.Popen(f'{ssh_command} -o ConnectTimeout=1 -M -N -q ', shell=True)

    # quickly check if we actually have a functional ssh connection (might not be the case right after an update)
    checkmsg = ssh('"/bin/true"')
    if checkmsg != "":
        print("ssh connection does not work, verify that you can manually ssh into your reMarkable. ssh itself commented the situation with:")
        print(checkmsg)
        sys.exit(255)

    retrieve_metadata()
    if args.mode == 'push':
        push_to_remarkable(**vars(args))
    elif args.mode == 'pull':
        pull_from_remarkable(**vars(args))
    elif args.mode == 'backup':
        args.documents = get_toplevel_files()
        pull_from_remarkable(**vars(args))
    else:
        print("Unknown mode, doing nothing.")
        print("Available modes are")
        print("    push:   push documents from this machine to the reMarkable")
        print("    pull:   pull documents from the reMarkable to this machine")
        print("    backup: pull all files from the remarkable to this machine (excludes still apply)")
finally:
    if ssh_connection is not None:
        ssh_connection.terminate()

