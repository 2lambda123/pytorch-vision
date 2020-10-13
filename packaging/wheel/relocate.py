# -*- coding: utf-8 -*-

"""Helper script to package wheels and relocate binaries."""

# Standard library imports
import os
import io
import sys
import glob
import shutil
import zipfile
import hashlib
import platform
import subprocess
import os.path as osp
from base64 import urlsafe_b64encode

# Third party imports
from auditwheel.lddtree import lddtree
from wheel.bdist_wheel import get_abi_tag


WHITELIST = {
    'libgcc_s.so.1', 'libstdc++.so.6', 'libm.so.6',
    'libdl.so.2', 'librt.so.1', 'libc.so.6',
    'libnsl.so.1', 'libutil.so.1', 'libpthread.so.0',
    'libresolv.so.2', 'libX11.so.6', 'libXext.so.6',
    'libXrender.so.1', 'libICE.so.6', 'libSM.so.6',
    'libGL.so.1', 'libgobject-2.0.so.0', 'libgthread-2.0.so.0',
    'libglib-2.0.so.0', 'ld-linux-x86-64.so.2', 'ld-2.17.so'
}

HERE = osp.dirname(osp.abspath(__file__))
PACKAGE_ROOT = osp.dirname(osp.dirname(HERE))
PLATFORM_ARCH = platform.machine()
PYTHON_VERSION = sys.version_info


def read_chunks(file, size=io.DEFAULT_BUFFER_SIZE):
    """Yield pieces of data from a file-like object until EOF."""
    while True:
        chunk = file.read(size)
        if not chunk:
            break
        yield chunk


def rehash(path, blocksize=1 << 20):
    """Return (hash, length) for path using hashlib.sha256()"""
    h = hashlib.sha256()
    length = 0
    with open(path, 'rb') as f:
        for block in read_chunks(f, size=blocksize):
            length += len(block)
            h.update(block)
    digest = 'sha256=' + urlsafe_b64encode(
        h.digest()
    ).decode('latin1').rstrip('=')
    # unicode/str python2 issues
    return (digest, str(length))  # type: ignore


def unzip_file(file, dest):
    """Decompress zip `file` into directory `dest`."""
    with zipfile.ZipFile(file, 'r') as zip_ref:
        zip_ref.extractall(dest)


def is_program_installed(basename):
    """
    Return program absolute path if installed in PATH.
    Otherwise, return None
    On macOS systems, a .app is considered installed if
    it exists.
    """
    if (sys.platform == 'darwin' and basename.endswith('.app') and
            osp.exists(basename)):
        return basename

    for path in os.environ["PATH"].split(os.pathsep):
        abspath = osp.join(path, basename)
        if osp.isfile(abspath):
            return abspath


def find_program(basename):
    """
    Find program in PATH and return absolute path
    Try adding .exe or .bat to basename on Windows platforms
    (return None if not found)
    """
    names = [basename]
    if os.name == 'nt':
        # Windows platforms
        extensions = ('.exe', '.bat', '.cmd')
        if not basename.endswith(extensions):
            names = [basename + ext for ext in extensions] + [basename]
    for name in names:
        path = is_program_installed(name)
        if path:
            return path


def patch_new_path(library_path, new_dir):
    library = osp.basename(library_path)
    name, *rest = library.split('.')
    rest = '.'.join(rest)
    hash_id = hashlib.sha256(library_path.encode('utf-8')).hexdigest()[:8]
    new_name = '.'.join([name, hash_id, rest])
    return osp.join(new_dir, new_name)


def relocate_library(patchelf, output_dir, output_library, binary):
    """
    Relocate a shared library to be packaged on a wheel.

    Given a shared library, find the transitive closure of its dependencies,
    rename and copy them into the wheel while updating their respective rpaths.
    """
    print('Relocating {0}'.format(binary))
    binary_path = osp.join(output_library, binary)

    ld_tree = lddtree(binary_path)
    tree_libs = ld_tree['libs']

    binary_queue = [(n, binary) for n in ld_tree['needed']]
    binary_paths = {binary: binary_path}
    binary_dependencies = {}

    while binary_queue != []:
        library, parent = binary_queue.pop(0)
        library_info = tree_libs[library]
        print(library)

        if library_info['path'] is None:
            print('Omitting {0}'.format(library))
            continue

        if library in WHITELIST:
            # Omit glibc/gcc/system libraries
            print('Omitting {0}'.format(library))
            continue

        parent_dependencies = binary_dependencies.get(parent, [])
        parent_dependencies.append(library)
        binary_dependencies[parent] = parent_dependencies

        if library in binary_paths:
            continue

        binary_paths[library] = library_info['path']
        binary_queue += [(n, library) for n in library_info['needed']]

    print('Copying dependencies to wheel directory')
    new_libraries_path = osp.join(output_dir, 'torchvision.libs')
    os.makedirs(new_libraries_path)

    new_names = {binary: binary_path}

    for library in binary_paths:
        if library != binary:
            library_path = binary_paths[library]
            new_library_path = patch_new_path(library_path, new_libraries_path)
            print('{0} -> {1}'.format(library, new_library_path))
            shutil.copyfile(library_path, new_library_path)
            new_names[library] = new_library_path

    print('Updating dependency names by new files')
    for library in binary_paths:
        if library != binary:
            if library not in binary_dependencies:
                continue
            library_dependencies = binary_dependencies[library]
            new_library_name = new_names[library]
            for dep in library_dependencies:
                new_dep = osp.basename(new_names[dep])
                print('{0}: {1} -> {2}'.format(library, dep, new_dep))
                subprocess.check_output(
                    [
                        patchelf,
                        '--replace-needed',
                        dep,
                        new_dep,
                        new_library_name
                    ],
                    cwd=new_libraries_path)

            print('Updating library rpath')
            subprocess.check_output(
                [
                    patchelf,
                    '--set-rpath',
                    "$ORIGIN",
                    new_library_name
                ],
                cwd=new_libraries_path)

            subprocess.check_output(
                [
                    patchelf,
                    '--print-rpath',
                    new_library_name
                ],
                cwd=new_libraries_path)

    print("Update library dependencies")
    library_dependencies = binary_dependencies[binary]
    for dep in library_dependencies:
        new_dep = osp.basename(new_names[dep])
        print('{0}: {1} -> {2}'.format(binary, dep, new_dep))
        subprocess.check_output(
            [
                patchelf,
                '--replace-needed',
                dep,
                new_dep,
                binary
            ],
            cwd=output_library)

    print('Update library rpath')
    subprocess.check_output(
        [
            patchelf,
            '--set-rpath',
            "$ORIGIN:$ORIGIN/../torchvision.libs",
            binary_path
        ],
        cwd=output_library
    )


def patch_linux():
    # Get patchelf location
    patchelf = find_program('patchelf')
    if patchelf is None:
        raise FileNotFoundError('Patchelf was not found in the system, please'
                                ' make sure that is available on the PATH.')

    # Find wheel
    print('Finding wheels...')
    wheels = glob.glob(osp.join(PACKAGE_ROOT, 'dist', '*.whl'))
    output_dir = osp.join(PACKAGE_ROOT, 'dist', '.wheel-process')

    if osp.exists(output_dir):
        shutil.rmtree(output_dir)

    os.makedirs(output_dir)

    image_binary = 'image.so'
    video_binary = 'video_reader.so'
    torchvision_binaries = [image_binary, video_binary]
    for wheel in wheels:
        print('Unzipping wheel...')
        wheel_file = osp.basename(wheel)
        wheel_dir = osp.dirname(wheel)
        print('{0}'.format(wheel_file))
        wheel_name, _ = osp.splitext(wheel_file)
        unzip_file(wheel, output_dir)

        print('Finding ELF dependencies...')
        output_library = osp.join(output_dir, 'torchvision')
        for binary in torchvision_binaries:
            if osp.exists(osp.join(output_library, binary)):
                relocate_library(patchelf, output_dir, output_library, binary)

        print('Update RECORD file in wheel')
        record_file = glob.glob(osp.join(output_dir, '*.dist-info'))[0]

        with open(record_file, 'w') as f:
            for root, _, files in os.walk(output_dir):
                for this_file in files:
                    full_file = osp.join(root, this_file)
                    rel_file = osp.relpath(full_file, output_dir)
                    if full_file == record_file:
                        f.write('{0},,\n'.format(rel_file))
                    else:
                        digest, size = rehash(full_file)
                        f.write('{0},{1},{2}\n'.format(rel_file, digest, size))

        print('Compressing wheel')
        shutil.make_archive(wheel_name, 'zip', output_dir)
        os.remove(wheel)
        shutil.move('{0}.zip'.format(osp.join(wheel_dir, wheel_name)), wheel)
        shutil.rmtree(output_dir)


if __name__ == '__main__':
    if sys.platform == 'linux':
        patch_linux()
