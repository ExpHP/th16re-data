from __future__ import print_function

# Script used to export these data files from binja.
#
# In binja's python console:
#
# >>> import exphp_export; exphp_export.run(bv)

import os
import re
import json
from collections import defaultdict
import contextlib
from binaryninja import Symbol, SymbolType, Type, log, BinaryViewType, TypeClass, FunctionParameter

BNDB_DIR = r"E:\Downloaded Software\Touhou Project"
JSON_DIR = r"F:\asd\clone\th16re-data\data"
MD5SUMS_FILENAME = 'md5sums.json'
ALL_MD5_KEYS = ['bndb', 'funcs.json', 'labels.json', 'statics.json', 'type-structs-own.json']
GAMES = [
    "th06.v1.02h",
    "th07.v1.00b",
    "th08.v1.00d",
    "th09.v1.50a",
    "th095.v1.02a",
    "th10.v1.00a",
    "th11.v1.00a",
    "th12.v1.00b",
    "th125.v1.00a",
    "th128.v1.00a",
    "th13.v1.00c",
    "th14.v1.00b",
    "th143.v1.00a",
    "th15.v1.00b",
    "th16.v1.00a",
    "th165.v1.00a",
    "th17.v1.00b",
    "th18.v1.00a",
]

def export(bv, path=r'F:\asd\clone\th16re-data\data\th16.v1.00a'):
    export_symbols(bv, path=path)
    export_types(bv, path=path)

def open_bv(path, **kw):
    # Note: This is now a context manager, hurrah!
    return BinaryViewType.get_view_of_file(path, **kw)

def compute_md5(path):
    import hashlib
    return hashlib.md5(open(path,'rb').read()).hexdigest()

def dict_get_path(d, parts, default=None):
    for part in parts:
        if part not in d:
            return default
        d = d[part]
    return d

def export_all(games=GAMES, bndb_dir=BNDB_DIR, json_dir=JSON_DIR, force=False, emit_status=print):
    # we will autocreate some subdirs, but for safety against mistakes
    # we won't create anything above the output dir itself
    require_dir_exists(os.path.dirname(json_dir))

    old_md5sums = {} if force else read_md5s_file(json_dir)

    for game in games:
        bndb_path = os.path.join(bndb_dir, f'{game}.bndb')
        json_subdir = os.path.join(json_dir, f'{game}')
        if compute_md5(bndb_path) == lookup_md5(old_md5sums, game, 'bndb'):
            emit_status(f"{game}: up to date")
            continue

        emit_status(f"{game}: exporting...")
        with open_bv(bndb_path, update_analysis=False) as bv:
            os.makedirs(json_subdir, exist_ok=True)
            export(bv, path=json_subdir)

        update_md5s(games=[game], keys=ALL_MD5_KEYS, bndb_dir=bndb_dir, json_dir=json_dir)
    emit_status("done")

def export_symbols(bv, path):
    # precompute expensive properties
    bv_data_vars = bv.data_vars
    bv_symbols = bv.symbols

    datas = []
    funcs = []
    labels = defaultdict(list)
    for name, symbol in bv_symbols.items():
        if isinstance(symbol, list):
            symbol = symbol[0]
        address = symbol.address
        if symbol.type == SymbolType.DataSymbol:
            # Skip statics that I didn't rename.
            if any(
                name.startswith(prefix) and is_hex(name[len(prefix):])
                for prefix in ['data_', 'jump_table_']
            ):
                continue

            if name in [
                '__dos_header', '__dos_stub', '__rich_header', '__coff_header',
                '__pe32_optional_header', '__section_headers',
            ]:
                continue

            # There's a large number of symbols autogenerated by binja for DLL functions.
            # Since they can be autogenerated, there's no point sharing them.
            if name.startswith('__import_') or name.startswith('__export_'):
                continue

            # My naming pattern for case labels.
            for infix in ['__case_', '__cases_']:
                if infix in name:
                    kind, rest = name.split(infix)
                    labels[kind].append((nice_hex(address), rest))
                    break
            else:
                try:
                    t = bv_data_vars[address].type
                except KeyError:
                    continue
                datas.append(dict(name=name, addr=nice_hex(address), type=str(t), comment=bv.get_comment_at(address) or None))

        elif symbol.type == SymbolType.FunctionSymbol:
            # Identify functions that aren't worth sharing
            def is_boring(name):
                # Done by binja, e.g. 'j_sub_45a83#4'
                if '#' in name:
                    return True

                # these suffixes don't convey enough info for the name to be worth sharing if there's nothing else
                name = strip_suffix(name, '_identical_twin')
                name = strip_suffix(name, '_twin')
                name = strip_suffix(name, '_sister')
                name = strip_suffix(name, '_sibling')

                # For some things I've done nothing more than change the prefix using a script
                for prefix in ['sub', 'leaf', 'seh', 'SEH', 'j_sub']:
                    if name.startswith(prefix + '_') and is_hex(name[len(prefix):].lstrip('_')):
                        return True
                return False

            if is_boring(name):
                continue

            funcs.append(dict(name=name, addr=nice_hex(address), comment=bv.get_comment_at(address) or None))

    datas.sort(key=lambda x: int(x['addr'], 16))
    funcs.sort(key=lambda x: int(x['addr'], 16))
    for v in labels.values():
        v.sort()

    with open_output_json_with_validation(os.path.join(path, 'statics.json')) as f:
        nice_json_array(f, 0, datas, lambda f, d: json.dump(with_key_order(d, ['addr', 'name', 'type', 'comment']), f))

    with open_output_json_with_validation(os.path.join(path, 'funcs.json')) as f:
        nice_json_array(f, 0, funcs, lambda f, d: json.dump(with_key_order(d, ['addr', 'name', 'comment']), f))

    with open_output_json_with_validation(os.path.join(path, 'labels.json')) as f:
        nice_json_object_of_array(f, 0, labels, lambda f, x: json.dump(x, f))

def strip_suffix(s, suffix):
    return s[:len(s)-len(suffix)] if s.endswith(suffix) else s

def nice_hex(x):
    s = hex(x)
    return s[:-1] if s.endswith('L') else s

def export_types(bv, path):
    type_ids = _gather_type_ids_and_classify(bv)

    # Create TypeTree abbreviations for all typedefs as long as they are valid names for abbreviations
    abbreviations = {
        str(k): {'ttree': {'type': 'named', 'name': str(k)}}
        for k in type_ids['typedefs']
    }
    abbreviation_type_ids = {str(k): v for (k, v) in type_ids['typedefs'].items() if k in abbreviations}

    ttree_converter = TypeToTTreeConverter(bv, abbreviation_type_ids)

    structures = {}
    for (type_name, type_id) in type_ids['structs'].items():
        structure = bv.get_type_by_id(type_id).structure
        structures[str(type_name)] = structure_to_cereal(structure, ttree_converter)

    enumerations = {}
    for (type_name, type_id) in type_ids['enums'].items():
        enumeration = bv.get_type_by_id(type_id).enumeration
        enumerations[str(type_name)] = [(m.name, m.value) for m in enumeration.members]

    typedefs = {}
    for (type_name, type_id) in type_ids['typedefs'].items():
        expanded_ty = bv.get_type_by_id(type_id)
        typedefs[str(type_name)] = {'size': expanded_ty.width, 'def': str(expanded_ty), 'ttree': ttree_converter.to_ttree(expanded_ty)}

    def find_bad(x, path=[], root=None, root1=None):
        if root is None:
            root = x
        elif root1 is None:
            root1 = x
        if isinstance(x, dict):
            for key in x:
                path.append(key)
                assert isinstance(key, str), path
                find_bad(x[key], path, root, root1)
                path.pop()
        elif isinstance(x, (list, tuple)):
            for i, item in enumerate(x):
                path.append(i)
                find_bad(item, path, root, root1)
                path.pop()
        elif x is None or isinstance(x, (int, str, bool, float)):
            return
        else:
            assert False, (type(x), path, root1)
    find_bad(structures)
    find_bad(enumerations)
    find_bad(typedefs)
    find_bad(abbreviations)
    find_bad(abbreviation_type_ids)


    with open_output_json_with_validation(os.path.join(path, 'type-aliases.json')) as f:
        nice_json_object(f, 0, typedefs, lambda f, d: json.dump(with_key_order(d, ['def', 'size', 'ttree']), f))

    # I name all of my structures starting with "z" so that they all appear together in binja,
    # which always sorts structures by name (and starts out preloaded with hundreds of
    # autogenerated structures from windows headers).
    #
    # Luckily that also makes them easy to identify in this script!
    structures_own = {k: v for (k, v) in structures.items() if k.startswith('z')}
    structures_ext = {k: v for (k, v) in structures.items() if not k.startswith('z')}
    with open_output_json_with_validation(os.path.join(path, 'ttree-abbreviations.json')) as f:
        nice_json_object(f, 0, typedefs, lambda f, d: json.dump(d, f))
    with open_output_json_with_validation(os.path.join(path, 'type-structs-own.json')) as f:
        nice_json_object_of_array(f, 0, structures_own, lambda f, d: json.dump(d, f))
    with open_output_json_with_validation(os.path.join(path, 'type-structs-ext.json')) as f:
        nice_json_object_of_array(f, 0, structures_ext, lambda f, d: json.dump(d, f))
    with open_output_json_with_validation(os.path.join(path, 'type-enums.json')) as f:
        nice_json_object_of_array(f, 0, enumerations, lambda f, d: json.dump(d, f))

def _gather_type_ids_and_classify(bv):
    out = {
        'structs': {},
        'unions': {},
        'enums': {},
        'typedefs': {},
    }
    types = bv.types
    for name in types:
        # structs, enums, and unions all produce objects representing the declaration
        if types[name].type_class == TypeClass.EnumerationTypeClass:
            out['enums'][name] = bv.get_type_id(name)
            continue
        if types[name].type_class == TypeClass.StructureTypeClass:
            if types[name].structure.union:
                out['unions'][name] = bv.get_type_id(name)
            else:
                out['structs'][name] = bv.get_type_id(name)
            continue

        # Here's where things start to get silly.
        #
        # For some bizarre reason, different kinds of typedefs either return a
        # NamedTypeReference representing the typedef itself, or a type representing the
        # expansion.
        if (
            types[name].type_class == TypeClass.NamedTypeReferenceClass
            and types[name].registered_name
            and types[name].registered_name.name == name
        ):
            # This is the typedef itself.  We want the expansion!
            out['typedefs'][name] = types[name].named_type_reference.type_id
        else:
            # This is probably the expansion.
            out['typedefs'][name] = bv.get_type_id(name)
    return out

def import_all_functions(games=GAMES, bndb_dir=BNDB_DIR, json_dir=JSON_DIR, emit_status=print):
    return _import_all_symbols(
        games=games, bndb_dir=bndb_dir, json_dir=json_dir, emit_status=print,
        json_filename='funcs.json', symbol_type=SymbolType.FunctionSymbol,
    )

def import_all_statics(games=GAMES, bndb_dir=BNDB_DIR, json_dir=JSON_DIR, emit_status=print):
    return _import_all_symbols(
        games=games, bndb_dir=bndb_dir, json_dir=json_dir, emit_status=print,
        json_filename='statics.json', symbol_type=SymbolType.DataSymbol,
    )

def _import_all_symbols(games, bndb_dir, json_dir, json_filename, symbol_type, emit_status):
    old_md5sums = read_md5s_file(json_dir)

    for game in games:
        bndb_path = os.path.join(bndb_dir, f'{game}.bndb')
        json_path = os.path.join(json_dir, f'{game}', json_filename)
        try:
            with open(json_path) as f:
                funcs_json = json.load(f)
        except (IOError, json.decoder.JSONDecodeError) as e:
            emit_status(f'{game}: {e}')
            continue

        if compute_md5(json_path) == lookup_md5(old_md5sums, game, json_filename):
            emit_status(f"{game}: up to date")
            continue

        emit_status(f"{game}: checking...")
        with open_bv(bndb_path, update_analysis=False) as bv:
            if _import_symbols_from_json(bv, funcs_json, symbol_type, emit_status=lambda s: emit_status(f'{game}: {s}')):
                emit_status(f'{game}: saving...')
                bv.save_auto_snapshot()

        update_md5s(games=[game], keys=['bndb', json_filename], bndb_dir=bndb_dir, json_dir=json_dir)
    emit_status("done")

def import_funcs_from_json(bv, funcs, emit_status=None):
    return _import_symbols_from_json(bv, funcs, SymbolType.FunctionSymbol, emit_status=emit_status)
def import_statics_from_json(bv, statics, emit_status=None):
    return _import_symbols_from_json(bv, statics, SymbolType.DataSymbol, emit_status=emit_status)

def _import_symbols_from_json(bv, symbols, symbol_type, emit_status=None):
    changed = False
    for d in symbols:
        addr = int(d['addr'], 16)
        name = d['name']
        existing = bv.get_symbol_at(addr)
        if existing is not None:
            if name == existing.name:
                continue
            else:
                bv.define_user_symbol(Symbol(symbol_type, addr, name))
                changed = True
                if emit_status:
                    emit_status(f'rename {existing.name} => {name}')
        else:
            bv.define_user_symbol(Symbol(symbol_type, addr, name))
            changed = True
            if emit_status:
                emit_status(f'name {existing.name}')
    return changed

def merge_function_files(games=GAMES, json_dir=JSON_DIR, emit_status=print):
    return _merge_symbol_files(games, json_dir, 'funcs.json', emit_status)
def merge_static_files(games=GAMES, json_dir=JSON_DIR, emit_status=print):
    return _merge_symbol_files(games, json_dir, 'statics.json', emit_status)

def _merge_symbol_files(games, json_dir, filename, emit_status):
    require_dir_exists(json_dir)
    os.makedirs(os.path.join(json_dir, 'composite'), exist_ok=True)

    composite_path = os.path.join(json_dir, 'composite', filename)
    composite_items = []
    for game in games:
        with open(os.path.join(json_dir, f'{game}/{filename}')) as f:
            game_items = json.load(f)
        composite_items.extend(dict(game=game, **d) for d in game_items)

    composite_items.sort(key=lambda d: d['name'])

    with open(composite_path, 'w') as f:
        nice_json_array(f, 0, composite_items, lambda f, d: json.dump(d, f))

def split_function_files(games=GAMES, json_dir=JSON_DIR, emit_status=print):
    return _split_symbol_files(games, json_dir, 'funcs.json', emit_status)
def split_static_files(games=GAMES, json_dir=JSON_DIR, emit_status=print):
    return _split_symbol_files(games, json_dir, 'statics.json', emit_status)

def _split_symbol_files(games, json_dir, filename, emit_status):
    require_dir_exists(json_dir)

    with open(os.path.join(json_dir, 'composite', filename)) as f:
        composite_items = json.load(f)
    for game in games:
        game_items = [dict(d) for d in composite_items if d['game'] == game]
        for d in game_items:
            del d['game']
        game_items.sort(key=lambda d: d['addr'])
        with open(os.path.join(json_dir, f'{game}/{filename}'), 'w') as f:
            nice_json_array(f, 0, game_items, lambda f, d: json.dump(d, f))

def structure_to_cereal(structure, ttree_converter):
    # Include a fake field at the max offset to help simplify things
    effective_members = [(x.offset, x.name, x.type) for x in structure.members]
    effective_members.append((structure.width, None, None))

    # Don't emit large filler char fields
    is_filler = lambda name, ty: name and ty and ty.element_type and name.startswith('_') and ty.element_type.width == 1 and ty.width > 64
    effective_members = [(off, name, ty) for (off, name, ty) in effective_members if not is_filler(name, ty)]

    # Unknown area at beginning
    output = []
    def add_row(offset, name, ty):
        ty_json = None if ty is None else ttree_converter.to_ttree(ty)
        output.append({'offset': nice_hex(offset), 'name': name, 'type': ty_json})

    if effective_members[0][0] != 0:
        add_row(0, '__unknown', None)

    for (offset, name, ty), (next_offset, _, _) in window2(effective_members):
        # ty_str = str(ty)

        # # Binja treats __convention("thiscall") function pointers weird W.R.T. type inference
        # # and I work around this by annotating them as __stdcall with '@ ecx' on the first argument instead.
        # if ' @ ecx' in ty_str and '__stdcall' in ty_str:
        #     ty_str = ty_str.replace(' @ ecx', '').replace('__stdcall', '__thiscall')

        add_row(offset, name, ty)
        unused_bytes = next_offset - offset - ty.width
        if unused_bytes:
            add_row(offset + ty.width, '__unknown', None)
    add_row(structure.width, '__end', None)
    return output

def nice_json_array(file, indent, arr, func):
    arr = list(arr)
    if not arr:
        print('[]', file=file)
        return

    first = True
    for x in arr:
        print(' ' * indent + ('[ ' if first else ', '), end='', file=file)
        first = False
        func(file, x)
        print(file=file)

    print(' ' * indent + ']', file=file)

def nice_json_object(file, indent, obj, func):
    if not obj:
        print('{}', file=file)
        return

    first = True
    for key in obj:
        print(' ' * indent + ('{ ' if first else ', '), end='', file=file)
        print(json.dumps(key) + ': ', end='', file=file)
        first = False
        func(file, obj[key])
        print(file=file)
    print(' ' * indent + '}', file=file)

def nice_json_object_of_array(file, indent, obj, func):
    if not obj:
        print('{}', file=file)
        return

    first = True
    for key in obj:
        print('{ ' if first else ', ', end='', file=file)
        print(json.dumps(key) + ': ', end='', file=file)
        print(file=file)
        first = False
        nice_json_array(file, indent+2, obj[key], func)
        print(file=file)
    print(' ' * indent + '}', file=file)

def nice_json_object_of_object(file, indent, obj, func):
    if not obj:
        print('{}', file=file)
        return

    first = True
    for key in obj:
        print('{ ' if first else ', ', end='', file=file)
        print(json.dumps(key) + ': ', end='', file=file)
        print(file=file)
        first = False
        nice_json_object(file, indent+2, obj[key], func)
        print(file=file)
    print(' ' * indent + '}', file=file)

#============================================================================

def read_md5s_file(json_dir=JSON_DIR):
    md5sum_path = os.path.join(json_dir, MD5SUMS_FILENAME)
    try:
        with open(md5sum_path) as f:
            return json.load(f)
    except IOError: return {}
    except json.decoder.JSONDecodeError: return {}

def update_md5s(games, keys, bndb_dir, json_dir):
    md5s = read_md5s_file(json_dir)

    path_funcs = {
        'bndb': (lambda game: os.path.join(bndb_dir, f'{game}.bndb')),
        'funcs.json': (lambda game: os.path.join(json_dir, game, 'funcs.json')),
        'labels.json': (lambda game: os.path.join(json_dir, game, 'labels.json')),
        'statics.json': (lambda game: os.path.join(json_dir, game, 'statics.json')),
        'type-structs-own.json': (lambda game: os.path.join(json_dir, game, 'type-structs-own.json')),
    }
    for game in games:
        if game not in md5s:
            md5s[game] = {}
        for key in keys:
            md5s[game][key] = compute_md5(path_funcs[key](game))

    with open(os.path.join(json_dir, MD5SUMS_FILENAME), 'w') as f:
        nice_json_object_of_object(f, 0, md5s, lambda f, d: json.dump(d, f))

def lookup_md5(md5s_dict, game, key):
    assert key in ALL_MD5_KEYS # protection against typos
    print(game, key)
    return md5s_dict.get(game, {}).get(key, None)

@contextlib.contextmanager
def open_output_json_with_validation(path):
    """ Open a file for writing json.  Once the 'with' block is exited, the file will be
    reopened for reading to validate the JSON. """
    with open(path, 'w') as f:
        yield f

    with open(path) as f:
        json.load(f) # this will fail if the JSON is invalid

#============================================================================

TTREE_VALID_ABBREV_REGEX = re.compile(r'^[_\$#:a-zA-Z][_\$#:a-zA-Z0-9]*$')

class TypeToTTreeConverter:
    def __init__(self, bv, abbrev_type_ids):
        self.bv = bv
        self.abbrev_type_ids = abbrev_type_ids

    def to_ttree(self, ty):
        return self._to_ttree_flat(ty)

    def _to_ttree_flat(self, ty):
        ttree = self._to_ttree_nested(ty)
        ttree = _possibly_flatten_nested_ttree(ttree)
        ttree = _further_abbreviate_flattened_ttree(ttree)
        return ttree

    # Produces a typetree where the outermost node is not a list.
    #
    # Recursive calls should use '_to_ttree_flat' if and only if they are a place that
    # cannot be chained through.  (e.g. an object field not called 'inner').
    # Otherwise they should use '_to_ttree_nested'.
    def _to_ttree_nested(self, ty):
        if ty.type_class == TypeClass.ArrayTypeClass:
            return {'type': 'array', 'len': ty.count, 'inner': self._to_ttree_nested(ty.element_type)}

        elif ty.type_class == TypeClass.PointerTypeClass:
            # FIXME this check should probably resolve NamedTypeReferences in the target,
            # in case there are typedefs to bare (non-ptr) function types.
            if ty.target.type_class == TypeClass.FunctionTypeClass:
                return self._function_ptr_type_to_ttree(ty.target)

            d = {'type': 'ptr', 'inner': self._to_ttree_nested(ty.target)}
            if ty.const:
                d['const'] = True
            return d

        elif ty.type_class == TypeClass.NamedTypeReferenceClass:
            if ty.registered_name is not None:
                # A raw typedef, instead of a reference to one.
                # Typically to get this, you'd have to call `bv.get_type_by_name` on a typedef name.
                #
                # It's not clear when type_to_ttree would ever be called with one.
                return {'type': 'named', 'name': str(ty.registered_name.name)}

            name = ty.named_type_reference.name
            target_type_id = ty.named_type_reference.type_id
            if self.abbrev_type_ids.get(name) == target_type_id:
                return str(name)
            else:
                log.log_error(f'Entered poorly-tested codepath! (a0db56ad-2b46-45dd-9f99-17cd20b1871f) {name}')
                # I have no idea how you enter this block, but there is a reasonable backup behavior
                # at our disposal.  (just follow the type reference)
                target_type = self.bv.get_type_by_id(target_type_id)
                return self._to_ttree_nested(target_type)

        elif ty.type_class in [TypeClass.StructureTypeClass, TypeClass.EnumerationTypeClass]:
            if ty.registered_name is not None:
                # A raw struct, enum, or union declaration, instead of a reference to one.
                #
                # It's not clear when type_to_ttree would ever be called with this.
                return {'type': 'named', 'name': str(ty.registered_name.name)}

            # an anonymous struct/union
            if ty.type_class ==TypeClass.EnumerationTypeClass:
                comment = 'inline enum'
            elif ty.structure.union:
                comment = 'inline union'
            else:
                comment = 'inline struct'
            return {'type': 'unsupported', 'size': ty.width, 'comment': comment}

        elif ty.type_class == TypeClass.VoidTypeClass:
            return {'type': 'void'}
        elif ty.type_class == TypeClass.IntegerTypeClass:
            prefix = 'i' if ty.signed else 'u'
            return f'{prefix}{ty.width * 8}'
        elif ty.type_class == TypeClass.FloatTypeClass:
            return f'f{ty.width * 8}'
        elif ty.type_class == TypeClass.BoolTypeClass:
            return f'u{ty.width * 8}'
        elif ty.type_class == TypeClass.WideCharTypeClass:
            return f'u{ty.width * 8}'

        elif ty.type_class == TypeClass.FunctionTypeClass:
            raise RuntimeError(f"bare FunctionTypeClass not supported (only function pointers): {ty}")
        elif ty.type_class == TypeClass.ValueTypeClass:
            # not sure where you get one of these
            raise RuntimeError(f"ValueTypeClass not supported: {ty}")
        elif ty.type_class == TypeClass.VarArgsTypeClass:
            # I don't know how you get this;  va_list is just an alias for char*,
            # and variadic functions merely set .has_variable_arguments = True.
            raise RuntimeError(f"VarArgsTypeClass not supported: {ty}")
        else:
            raise RuntimeError(f"Unsupported type {ty}")

    def _function_ptr_type_to_ttree(self, func_ty):
        parameters = list(func_ty.parameters)
        abi = func_ty.calling_convention and str(func_ty.calling_convention)

        if (abi == 'stdcall'
            and parameters
            and parameters[0].location
            and parameters[0].location.name == 'ecx'
            and not any(p.location for p in parameters[1:])
        ):
            abi = 'fastcall'
            parameters[0] = FunctionParameter(parameters[0].type, parameters[0].name)  # remove location

        def convert_parameter(p):
            out = {'ty': self._to_ttree_flat(p.type)}
            if p.name:
                out['name'] = p.name
            return out

        out = {'type': 'fn-ptr'}

        if abi:
            out['abi'] = abi

        out['ret'] = self._to_ttree_flat(func_ty.return_value)

        if parameters:
            out['params'] = list(map(convert_parameter, parameters))

        return out

# Turn a nested object ttree into a list. (destructively)
def _possibly_flatten_nested_ttree(ttree):
    if isinstance(ttree, dict) and 'inner' in ttree:
        flattened = []
        while isinstance(ttree, dict) and 'inner' in ttree:
            flattened.append(ttree)
            ttree = ttree.pop('inner')
        flattened.append(ttree)
        return flattened
    return ttree

assert (
    _possibly_flatten_nested_ttree({'a': 1, 'inner': {'b': 2, 'inner': {'c': 3}}})
    == [{'a': 1}, {'b': 2}, {'c': 3}]
)

def _further_abbreviate_flattened_ttree(ttree):
    if isinstance(ttree, list):
        out = []
        for x in ttree:
            if x == {'type': 'ptr'}:
                out.append('*')
            elif isinstance(x, dict) and len(x) == 2 and x['type'] == 'array':
                out.append(x['len'])
            else:
                out.append(x)
        return out
    return ttree

# Turn a list ttree into a nested object. (destructively)
def _possibly_nest_flattened_ttree(ttree):
    if isinstance(ttree, list):
        out = ttree.pop()
        while ttree:
            new_out = ttree.pop()
            assert isinstance(new_out, dict) and 'inner' not in new_out
            new_out['inner'] = out
            out = new_out
        return out
    return ttree

#============================================================================

def window2(it):
    it = iter(it)
    prev = next(it)
    for x in it:
        yield prev, x
        prev = x

def with_key_order(d, keys):
    """ Set order of keys in a dict. (Python 3.7+ only) """
    return { k:d[k] for k in keys }

def require_dir_exists(path):
    if not os.path.exists(path):
        raise IOError(f"{path}: No such directory")

def is_hex(s):
    try:
        int('0x' + s, 16)
    except ValueError:
        return False
    return True
