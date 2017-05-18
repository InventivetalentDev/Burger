#!/usr/bin/env python
# -*- coding: utf8 -*-
"""
Copyright (c) 2011 Tyler Kenendy <tk@tkte.ch>

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""

from .topping import Topping

from jawa.util.descriptor import method_descriptor
from jawa.constants import *
from jawa.cf import ClassFile

try:
    from cStringIO import StringIO
except ImportError:
    from StringIO import StringIO

try:
    import json
except ImportError:
    import simplejson as json

class RecipesTopping(Topping):
    """Provides a list of most possible crafting recipes."""

    PROVIDES = [
        "recipes"
    ]

    DEPENDS = [
        "identify.recipe.superclass",
        "identify.block.list",
        "identify.item.list",
        "blocks",
        "items"
    ]

    @staticmethod
    def act(aggregate, jar, verbose=False):
        if "assets/minecraft/recipes/stick.json" in jar.namelist():
            recipe_list = RecipesTopping.find_from_json(aggregate, jar, verbose)
        else:
            recipe_list = RecipesTopping.find_from_jar(aggregate, jar, verbose)

        recipes = aggregate.setdefault("recipes", {})

        for recipe in recipe_list:
            makes = recipe['makes']['name']

            recipes_for_item = recipes.setdefault(makes, [])
            recipes_for_item.append(recipe)

    @staticmethod
    def find_from_json(aggregate, jar, verbose):
        if verbose:
            print "Extracting recipes from JSON"

        recipes = []

        def parse_item(blob):
            """ Converts a JSON item into a burger item"""
            # TODO: This converts a list into a single item, in a bad, data-losing way.
            # However, handling it more cleanly is hard, due to backwards compatibility.
            # As such, a TOTAL HACK of taking just the first element is used.
            if isinstance(blob, list):
                blob = blob[0]
            # There's some wierd stuff regarding 0 or 32767 here; I'm not worrying about it though
            # Probably 0 is the default for results, and 32767 means "any" for ingredients
            assert "item" in blob
            result = {}

            id = blob["item"]
            if id.startswith("minecraft:"):
                id = id[len("minecraft:"):] # TODO: In the future, we don't want to strip namespaces

            result["name"] = id
            # TODO: Do we need the type and data fields anymore?  They're fairly redundant (and don't reflect ingame behavior anymore)
            # Check if it's a block
            if id in aggregate["blocks"]["block"]:
                result["data"] = aggregate["blocks"]["block"][id]
                result["type"] = "block"
            else:
                result["data"] = aggregate["items"]["item"][id]
                result["type"] = "item"

            if "data" in blob:
                result["metadata"] = blob["data"]
            if "count" in blob:
                result["count"] = blob["count"]

            return result

        for name in jar.namelist():
            if name.startswith("assets/minecraft/recipes/") and name.endswith(".json"):
                data = json.loads(jar.read(name))
                recipe_id = "minecraft:" + name[len("assets/minecraft/recipes/"):-len(".json")]

                assert "type" in data
                assert "result" in data

                recipe = {}
                recipe["makes"] = parse_item(data["result"])
                if "count" not in recipe["makes"]:
                    recipe["makes"]["count"] = 1 # default, TODO should we keep specifying this?

                if data["type"] == "crafting_shapeless":
                    recipe["type"] = 'shapeless'

                    assert "ingredients" in data

                    recipe["ingredients"] = [parse_item(ingredient) for ingredient in data["ingredients"]]
                elif data["type"] == "crafting_shaped":
                    recipe["type"] = 'shape'

                    assert "pattern" in data
                    assert "key" in data

                    pattern = data["pattern"]
                    key = {id: parse_item(value) for (id, value) in data["key"].iteritems()}
                    recipe["raw"] = {
                        "rows": pattern,
                        "subs": key
                    }

                    shape = []
                    for row in pattern:
                        shape_row = []
                        for char in row:
                            if not char.isspace():
                                shape_row.append(key[char])
                            else:
                                shape_row.append(None)
                        shape.append(shape_row)
                    recipe["shape"] = shape
                else:
                    raise Exception("Unknown or invalid recipe type", data[type], "for recipe", recipe_id)

                recipe["id"] = recipe_id # new for 1.12, but used ingame
                recipes.append(recipe)

        return recipes

    @staticmethod
    def find_from_jar(aggregate, jar, verbose):
        superclass = aggregate["classes"]["recipe.superclass"]

        if verbose:
            print "Extracting recipes from", superclass

        cf = ClassFile(StringIO(jar.read(superclass + ".class")))

        # Find the constructor
        method = cf.methods.find_one(
            name="<init>"
        )

        # Find the set function, so we can figure out what class defines
        # a recipe.
        # This method's second parameter is an array of objects.
        setters = list(cf.methods.find(
            f = lambda m: len(m.args) == 2 and m.args[1].dimensions == 1 and m.args[1].name == "java/lang/Object"
        ))

        itemstack = aggregate["classes"]["itemstack"]

        target_class = setters[0].args[0]
        setter_names = [x.name.value for x in setters]

        def get_material(clazz, field):
            """Converts a class name and field into a block or item."""
            if clazz == aggregate["classes"]["block.list"]:
                if field in aggregate["blocks"]["block_fields"]:
                    name = aggregate["blocks"]["block_fields"][field]
                    data = aggregate["blocks"]["block"][name]
                    return {
                        'type': 'block',
                        'name': name,
                        'data': data
                    }
                else:
                    raise Exception("Unknown block with field " + field)
            elif clazz == aggregate["classes"]["item.list"]:
                if field in aggregate["items"]["item_fields"]:
                    name = aggregate["items"]["item_fields"][field]
                    data = aggregate["items"]["item"][name]
                    return {
                        'type': 'item',
                        'name': name,
                        'data': data
                    }
                else:
                    raise Exception("Unknown item with field " + field)
            else:
                raise Exception("Unknown list class " + clazz)

        def read_itemstack(itr):
            """Reads an itemstack from the given iterator of instructions"""
            stack = []
            while True:
                ins = itr.next()
                if ins.mnemonic.startswith("iconst_"):
                    stack.append(int(ins.mnemonic[-1]))
                elif ins.mnemonic == "bipush":
                    stack.append(ins.operands[0].value)
                elif ins.mnemonic == "getstatic":
                    const = cf.constants.get(ins.operands[0].value)
                    clazz = const.class_.name.value
                    name = const.name_and_type.name.value
                    stack.append((clazz, name))
                elif ins.mnemonic == "invokevirtual":
                    # TODO: This is a _total_ hack...
                    # We assume that this is an enum, used to get the data value
                    # for the given block.  We also assume that the return value
                    # matches the enum constant's position... and do math from that.
                    name = stack.pop()[1]
                    # As I said... ugly.  There's probably a way better way of doing this.
                    dv = int(name, 36) - int('a', 36)
                    stack.append(dv)
                elif ins.mnemonic == "iadd":
                    # For whatever reason, there are a few cases where 4 is both
                    # added and subtracted to the enum constant value.
                    # So we need to handle that :/
                    i2 = stack.pop()
                    i1 = stack.pop()
                    stack.append(i1 + i2);
                elif ins.mnemonic == "isub":
                    i2 = stack.pop()
                    i1 = stack.pop()
                    stack.append(i1 - i2);
                elif ins.mnemonic == "invokespecial":
                    const = cf.constants.get(ins.operands[0].value)
                    if const.name_and_type.name.value == "<init>":
                        break

            item = get_material(*stack[0])
            if len(stack) == 3:
                item['count'] = stack[1]
                item['metadata'] = stack[2]
            elif len(stack) == 2:
                item['count'] = stack[1]
            return item

        def find_recipes(jar, cf, method, target_class, setter_names):
            # Go through all instructions.
            itr = iter(method.code.disassemble())
            recipes = []
            try:
                while True:
                    ins = itr.next()
                    if ins.mnemonic != "new":
                        # Wait until an item starts
                        continue
                    # Start of another recipe - the ending item.
                    const = cf.constants.get(ins.operands[0].value)
                    if const.name.value != itemstack:
                        # Or it could be another type; irrelevant
                        continue
                    # The crafted item, first parameter
                    crafted_item = read_itemstack(itr)

                    ins = itr.next()
                    # Size of the parameter array
                    if ins.mnemonic.startswith("iconst_"):
                        param_count = int(ins.mnemonic[-1])
                    elif ins.mnemonic == "bipush":
                        param_count = ins.operands[0].value
                    else:
                        raise Exception('Unexpected instruction: expected int constant, got ' + str(ins))

                    num_astore = 0
                    data = None
                    array = []
                    while num_astore < param_count:
                        ins = itr.next()
                        # Read through the array; some strangeness of types,
                        # though.  Also, note that the array index is pushed,
                        # but we overwrite it with the second value and just
                        # add in order instead.
                        if ins.mnemonic == "aastore":
                            num_astore += 1
                            array.append(data)
                            data = None
                        elif ins.mnemonic in ("ldc", "ldc_w"):
                            const = cf.constants.get(ins.operands[0].value)
                            data = const.string.value
                        elif ins.mnemonic.startswith("iconst_"):
                            data = int(ins.mnemonic[-1])
                        elif ins.mnemonic == "bipush":
                            data = ins.operands[0].value
                        elif ins.mnemonic == "invokestatic":
                            const = cf.constants.get(ins.operands[0].value)
                            if const.class_.name.value == "java/lang/Character" and const.name_and_type.name.value == "valueOf":
                                data = chr(data)
                            else:
                                raise Exception("Unknown method invocation: " + repr(const))
                        elif ins.mnemonic == "getstatic":
                            const = cf.constants.get(ins.operands[0].value)
                            clazz = const.class_.name.value
                            field = const.name_and_type.name.value
                            data = get_material(clazz, field)
                        elif ins.mnemonic == "new":
                            data = read_itemstack(itr)

                    ins = itr.next()
                    assert ins.mnemonic == "invokevirtual"
                    const = cf.constants.get(ins.operands[0].value)

                    recipe_data = {}
                    if const.name_and_type.name.value == setter_names[0]:
                        # Shaped
                        recipe_data['type'] = 'shape'
                        recipe_data['makes'] = crafted_item
                        rows = []
                        subs = {}
                        # TODO: Is there a better way to distinguish chars
                        # and strings?  Right now, chars seem to be strings,
                        # except that the strings that come from jawa are
                        # unicode ones and the ones that come from chr() are
                        # just 'str'...
                        try:
                            itr2 = iter(array)
                            while True:
                                obj = itr2.next()
                                if isinstance(obj, unicode):
                                    # Pattern
                                    rows.append(obj)
                                elif isinstance(obj, str):
                                    # Character
                                    item = itr2.next()
                                    subs[obj] = item
                        except StopIteration:
                            pass
                        recipe_data['raw'] = {
                            'rows': rows,
                            'subs': subs
                        }

                        shape = []
                        for row in rows:
                            shape_row = []
                            for char in row:
                                if not char.isspace():
                                    shape_row.append(subs[char])
                                else:
                                    shape_row.append(None)
                            shape.append(shape_row)

                        recipe_data['shape'] = shape
                    else:
                        # Shapeless
                        recipe_data['type'] = 'shapeless'
                        recipe_data['makes'] = crafted_item
                        recipe_data['ingredients'] = array

                    recipes.append(recipe_data)
            except StopIteration:
                pass
            return recipes

        return find_recipes(jar, cf, method, target_class, setter_names)
