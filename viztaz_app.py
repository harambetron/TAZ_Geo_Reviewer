import os, glob
import geopandas as gpd
from shapely.geometry import Polygon, MultiPolygon
import numpy as np

from bokeh.io import curdoc
from bokeh.layouts import column, row, Spacer
from bokeh.models import (
    ColumnDataSource, DataTable, TableColumn,
    Div, TextInput, Button, Select, HoverTool
)
from bokeh.models.widgets.tables import HTMLTemplateFormatter
from bokeh.plotting import figure
from bokeh.tile_providers import CARTODBPOSITRON, ESRI_IMAGERY

# -----------------------------------------------------------------------------
# 1) Read Shapefiles
# -----------------------------------------------------------------------------
old_taz_folder = "./shapefiles/old_taz_shapefile"
new_taz_folder = "./shapefiles/new_taz_shapefile"
blocks_folder  = "./shapefiles/blocks_shapefile"

def find_shapefile_in_folder(folder):
    shp_files = glob.glob(os.path.join(folder, "*.shp"))
    if not shp_files:
        raise FileNotFoundError(f"No shapefile found in folder: {folder}")
    return shp_files[0]

url_old_taz = find_shapefile_in_folder(old_taz_folder)
url_new_taz = find_shapefile_in_folder(new_taz_folder)
url_blocks  = find_shapefile_in_folder(blocks_folder)

gdf_old_taz  = gpd.read_file(url_old_taz)
gdf_new_taz  = gpd.read_file(url_new_taz)
gdf_blocks   = gpd.read_file(url_blocks)

def remove_zero_geoms(gdf):
    def is_zero_bbox(geom):
        if geom is None or geom.is_empty:
            return True
        minx, miny, maxx, maxy = geom.bounds
        return (minx==0 and miny==0 and maxx==0 and maxy==0)
    return gdf[~gdf.geometry.apply(is_zero_bbox)].copy()

gdf_old_taz  = remove_zero_geoms(gdf_old_taz)
gdf_new_taz  = remove_zero_geoms(gdf_new_taz)
gdf_blocks   = remove_zero_geoms(gdf_blocks)

# Convert to EPSG:3857 if needed
if gdf_old_taz.crs is None or gdf_old_taz.crs.to_string() != "EPSG:3857":
    gdf_old_taz = gdf_old_taz.to_crs(epsg=3857)
if gdf_new_taz.crs is None or gdf_new_taz.crs.to_string() != "EPSG:3857":
    gdf_new_taz = gdf_new_taz.to_crs(epsg=3857)
if gdf_blocks.crs is None or gdf_blocks.crs.to_string() != "EPSG:3857":
    gdf_blocks = gdf_blocks.to_crs(epsg=3857)

# Rename columns if needed
if 'taz_id' not in gdf_old_taz.columns:
    if 'TAZ_ID' in gdf_old_taz.columns:
        gdf_old_taz = gdf_old_taz.rename(columns={'TAZ_ID':'taz_id'})

rename_map_new = {}
if 'taz_new1' in gdf_new_taz.columns:
    rename_map_new['taz_new1'] = 'taz_id'
if 'hh19' in gdf_new_taz.columns:
    rename_map_new['hh19'] = 'HH19'
if 'persns19' in gdf_new_taz.columns:
    rename_map_new['persns19'] = 'PERSNS19'
if 'workrs19' in gdf_new_taz.columns:
    rename_map_new['workrs19'] = 'WORKRS19'
if 'emp19' in gdf_new_taz.columns:
    rename_map_new['emp19']  = 'EMP19'
if 'hh49' in gdf_new_taz.columns:
    rename_map_new['hh49'] = 'HH49'
if 'persns49' in gdf_new_taz.columns:
    rename_map_new['persns49'] = 'PERSNS49'
if 'workrs49' in gdf_new_taz.columns:
    rename_map_new['workrs49'] = 'WORKRS49'
if 'emp49' in gdf_new_taz.columns:
    rename_map_new['emp49']  = 'EMP49'
if rename_map_new:
    gdf_new_taz = gdf_new_taz.rename(columns=rename_map_new)

if 'GEOID20' in gdf_blocks.columns:
    gdf_blocks = gdf_blocks.rename(columns={'GEOID20':'BLOCK_ID'})

# -----------------------------------------------------------------------------
# 2) Helper Functions
# -----------------------------------------------------------------------------
def remove_empty_or_none(geom):
    return geom is not None and not geom.is_empty

def split_multipolygons_to_cds(gdf, id_field, ensure_cols=None):
    if ensure_cols is None:
        ensure_cols = []
    for c in ensure_cols:
        if c not in gdf.columns:
            gdf[c] = None

    all_xs, all_ys, all_ids = [], [], []
    attr_data = {c: [] for c in ensure_cols}

    for _, row in gdf.iterrows():
        geom = row.geometry
        if not remove_empty_or_none(geom):
            continue
        row_id = str(row[id_field])
        row_attrs = {c: row[c] for c in ensure_cols}

        if geom.geom_type == "MultiPolygon":
            for subpoly in geom.geoms:
                xs, ys = subpoly.exterior.coords.xy
                all_xs.append(xs.tolist())
                all_ys.append(ys.tolist())
                all_ids.append(row_id)
                for c in ensure_cols:
                    attr_data[c].append(row_attrs[c])
        elif geom.geom_type == "Polygon":
            xs, ys = geom.exterior.coords.xy
            all_xs.append(xs.tolist())
            all_ys.append(ys.tolist())
            all_ids.append(row_id)
            for c in ensure_cols:
                attr_data[c].append(row_attrs[c])

    data = {'xs': all_xs, 'ys': all_ys, 'id': all_ids}
    for c in ensure_cols:
        data[c] = attr_data[c]
    return ColumnDataSource(data)

def filter_old_taz(old_id_int):
    """Returns subset of old TAZ, plus new TAZ and blocks that intersect 1-km buffer."""
    subset_old = gdf_old_taz[gdf_old_taz['taz_id'] == old_id_int]
    if subset_old.empty:
        return None, None, None

    old_union = subset_old.unary_union
    centroid = old_union.centroid
    buffer_geom = centroid.buffer(1000)  # 1 km

    new_sub = gdf_new_taz[gdf_new_taz.intersects(buffer_geom)].copy()
    blocks_sub = gdf_blocks[gdf_blocks.intersects(buffer_geom)].copy()

    return (subset_old, new_sub, blocks_sub)

def add_sum_row(d, colnames):
    """Append a 'Sum' row (bolded) even if no items => 0."""
    if 'id' not in d:
        d['id'] = []
        for c in colnames:
            d[c] = []
    sums = {c:0 for c in colnames}
    for c in colnames:
        for val in d[c]:
            if isinstance(val, (int, float)):
                sums[c] += val
    d['id'].append("Sum")
    for c in colnames:
        d[c].append(sums[c])
    return d

# -----------------------------------------------------------------------------
# 3) DataSources
# -----------------------------------------------------------------------------
old_taz_source        = ColumnDataSource(dict(xs=[], ys=[], id=[]))
old_taz_blocks_source = ColumnDataSource(dict(xs=[], ys=[], id=[]))

new_taz_source = ColumnDataSource(dict(xs=[], ys=[], id=[],
                                       HH19=[], PERSNS19=[], WORKRS19=[], EMP19=[],
                                       HH49=[], PERSNS49=[], WORKRS49=[], EMP49=[]))

new_taz_blocks_source = ColumnDataSource(dict(xs=[], ys=[], id=[]))

blocks_source = ColumnDataSource(dict(xs=[], ys=[], id=[],
                                      HH19=[], PERSNS19=[], WORKRS19=[], EMP19=[],
                                      HH49=[], PERSNS49=[], WORKRS49=[], EMP49=[]))

combined_old_source    = ColumnDataSource(dict(xs=[], ys=[], id=[]))
combined_new_source    = ColumnDataSource(dict(xs=[], ys=[], id=[],
                                               HH19=[], PERSNS19=[], WORKRS19=[], EMP19=[],
                                               HH49=[], PERSNS49=[], WORKRS49=[], EMP49=[]))
combined_blocks_source = ColumnDataSource(dict(xs=[], ys=[], id=[]))

old_taz_buffer_source    = ColumnDataSource(dict(xs=[], ys=[], id=[]))
old_taz_neighbors_source = ColumnDataSource(dict(xs=[], ys=[], id=[]))

# -----------------------------------------------------------------------------
# 4) Figures
# -----------------------------------------------------------------------------
TOOLS = "pan,wheel_zoom,box_zoom,reset"

# Use "scale_both" or "stretch_both" to have them expand. "match_aspect=True" to keep shape ratio.
p_old = figure(
    match_aspect=True, tools=TOOLS, active_scroll='wheel_zoom',
    x_axis_type="mercator", y_axis_type="mercator",
    sizing_mode="scale_both"
)
p_new = figure(
    match_aspect=True, tools=TOOLS + ",tap", active_scroll='wheel_zoom',
    x_axis_type="mercator", y_axis_type="mercator",
    sizing_mode="scale_both"
)
p_combined = figure(
    match_aspect=True, tools=TOOLS, active_scroll='wheel_zoom',
    x_axis_type="mercator", y_axis_type="mercator",
    sizing_mode="scale_both"
)
p_blocks = figure(
    match_aspect=True, tools=TOOLS + ",tap", active_scroll='wheel_zoom',
    x_axis_type="mercator", y_axis_type="mercator",
    sizing_mode="scale_both"
)

# External Div titles (always visible)
div_old_title      = Div(text="<b>1) Old TAZ (blue)</b>", styles={'font-size': '16px'})
div_new_title      = Div(text="<b>2) New TAZ (red outlines)</b>", styles={'font-size': '16px'})
div_combined_title = Div(text="<b>3) Combined (new=red, old=blue, blocks=yellow)</b>", styles={'font-size': '16px'})
div_blocks_title   = Div(text="<b>4) Blocks (selectable, yellow)</b>", styles={'font-size': '16px'})

tile_map = {}
def add_tiles():
    for f in [p_old, p_new, p_combined, p_blocks]:
        tile = f.add_tile(CARTODBPOSITRON)
        tile_map[f] = tile

add_tiles()

# -----------------------------------------------------------------------------
# 5) Patch Glyphs
# -----------------------------------------------------------------------------
# Old TAZ (panel #1)
p_old.patches(
    xs="xs", ys="ys", source=old_taz_source,
    fill_color=None, line_color="blue", line_width=2
)
p_old.patches(
    xs="xs", ys="ys", source=old_taz_blocks_source,
    fill_color=None, line_color="black", line_width=2, line_dash='dotted'
)

# 1 km buffer around old TAZ
buffer_renderer = p_old.patches(
    xs="xs", ys="ys", source=old_taz_buffer_source,
    fill_color="lightblue", fill_alpha=0.3, line_color=None
)
p_old.renderers.remove(buffer_renderer)
p_old.renderers.insert(0, buffer_renderer)

p_old.add_tools(HoverTool(renderers=[buffer_renderer], tooltips=[("Old TAZ ID", "@id")]))

# Gray dotted neighbor outlines
neighbors_renderer = p_old.patches(
    xs="xs", ys="ys", source=old_taz_neighbors_source,
    fill_color=None, line_color="gray", line_width=2, line_dash="dotted"
)
p_old.renderers.remove(neighbors_renderer)
p_old.renderers.insert(1, neighbors_renderer)

# New TAZ (panel #2) – red boundary w/ yellow fill on selection
taz_glyph_new = p_new.patches(
    xs="xs", ys="ys", source=new_taz_source,
    fill_color=None, line_color="red", line_width=2,
    selection_fill_color="yellow", selection_fill_alpha=0.3,
    selection_line_color="red", selection_line_width=2,
    nonselection_fill_color=None, nonselection_line_color="red"
)
p_new.patches(
    xs="xs", ys="ys", source=new_taz_blocks_source,
    fill_color=None, line_color="black", line_width=2, line_dash='dotted'
)
p_new.add_tools(HoverTool(tooltips=[("New TAZ ID","@id"), ("EMP19","@EMP19")], renderers=[taz_glyph_new]))

# Combined (panel #3)
p_combined.patches(xs="xs", ys="ys", source=combined_new_source,
                   fill_color=None, line_color="red", line_width=2)
p_combined.patches(xs="xs", ys="ys", source=combined_old_source,
                   fill_color=None, line_color="blue", line_width=2)
p_combined.patches(xs="xs", ys="ys", source=combined_blocks_source,
                   fill_color="yellow", fill_alpha=0.3,
                   line_color="black", line_width=2, line_dash='dotted')

# Blocks (panel #4)
p_blocks.patches(
    xs="xs", ys="ys", source=blocks_source,
    fill_color="yellow", fill_alpha=0.3,
    line_color="black", line_width=2, line_dash='dotted', line_alpha=.85,
    selection_fill_color="yellow", selection_fill_alpha=0.3,
    selection_line_color="black", selection_line_dash='dotted',
    nonselection_fill_alpha=0.10, nonselection_line_color="black",
    nonselection_line_dash='dotted', nonselection_line_alpha=0.85
)

# -----------------------------------------------------------------------------
# 6) Tables (horizontally scrollable, Sum row)
# -----------------------------------------------------------------------------
sum_template = """
<% if (id == 'Sum') { %>
<b><%= value %></b>
<% } else { %>
<%= value %>
<% } %>
"""
bold_formatter = HTMLTemplateFormatter(template=sum_template)

table_cols = [
    TableColumn(field="id",       title="ID",       formatter=bold_formatter, width=40),
    TableColumn(field="HH19",     title="HH19",     formatter=bold_formatter, width=70),
    TableColumn(field="PERSNS19", title="PERSNS19", formatter=bold_formatter, width=70),
    TableColumn(field="WORKRS19", title="WORKRS19", formatter=bold_formatter, width=70),
    TableColumn(field="EMP19",    title="EMP19",    formatter=bold_formatter, width=70),
    TableColumn(field="HH49",     title="HH49",     formatter=bold_formatter, width=70),
    TableColumn(field="PERSNS49", title="PERSNS49", formatter=bold_formatter, width=70),
    TableColumn(field="WORKRS49", title="WORKRS49", formatter=bold_formatter, width=70),
    TableColumn(field="EMP49",    title="EMP49",    formatter=bold_formatter, width=70),
]

new_taz_table_source = ColumnDataSource(dict(id=[],
                                             HH19=[], PERSNS19=[], WORKRS19=[], EMP19=[],
                                             HH49=[], PERSNS49=[], WORKRS49=[], EMP49=[]))
blocks_table_source  = ColumnDataSource(dict(id=[],
                                             HH19=[], PERSNS19=[], WORKRS19=[], EMP19=[],
                                             HH49=[], PERSNS49=[], WORKRS49=[], EMP49=[]))

new_taz_data_table = DataTable(
    source=new_taz_table_source,
    columns=table_cols,
    width=550,
    height=300,
    fit_columns=False
)
blocks_data_table  = DataTable(
    source=blocks_table_source,
    columns=table_cols,
    width=550,
    height=300,
    fit_columns=False
)

def update_new_taz_table():
    inds = new_taz_source.selected.indices
    d = {"id":[], "HH19":[], "PERSNS19":[], "WORKRS19":[], "EMP19":[],
         "HH49":[], "PERSNS49":[], "WORKRS49":[], "EMP49":[]}
    if inds:
        for c in d.keys():
            if c in new_taz_source.data:
                d[c] = [new_taz_source.data[c][i] for i in inds]
    d = add_sum_row(d, ["HH19","PERSNS19","WORKRS19","EMP19","HH49","PERSNS49","WORKRS49","EMP49"])
    new_taz_table_source.data = d

def update_blocks_table():
    inds = blocks_source.selected.indices
    d = {"id":[], "HH19":[], "PERSNS19":[], "WORKRS19":[], "EMP19":[],
         "HH49":[], "PERSNS49":[], "WORKRS49":[], "EMP49":[]}
    if inds:
        for c in d.keys():
            d[c] = [blocks_source.data[c][i] for i in inds]
    d = add_sum_row(d, ["HH19","PERSNS19","WORKRS19","EMP19","HH49","PERSNS49","WORKRS49","EMP49"])
    blocks_table_source.data = d

new_taz_source.selected.on_change("indices", lambda attr, old, new: update_new_taz_table())
blocks_source.selected.on_change("indices",  lambda attr, old, new: update_blocks_table())

# -----------------------------------------------------------------------------
# 7) UI: top row = "Enter Old TAZ ID" / text, right = "Selected BG"
#          second row = "Search TAZ", "Match 1st Panel Zoom"
# -----------------------------------------------------------------------------
label_taz    = Div(text="<b>Enter Old TAZ ID:</b>", width=120)
text_input   = TextInput(value="", title="", placeholder="TAZ ID...", width=100)
search_button= Button(label="Search TAZ", button_type="success", width=80)
match_zoom_btn=Button(label="Match 1st Panel Zoom", width=130)

tile_label   = Div(text="<b>Selected Map Background:</b>", width=150)
tile_select  = Select(value="CartoDB Positron", options=["CartoDB Positron","ESRI Satellite"], width=140)

def run_search():
    val = text_input.value.strip()
    if not val:
        # No label to update, just return
        return
    try:
        old_id_int = int(val)
    except ValueError:
        # Invalid integer => do nothing
        return

    old_sub, new_sub, blocks_sub = filter_old_taz(old_id_int)
    if old_sub is None or old_sub.empty:
        # Not found => do nothing
        return

    # 1 km buffer (for drawing)
    old_union = old_sub.unary_union
    centroid = old_union.centroid
    buffer_geom = centroid.buffer(1000)
    xs_buffer, ys_buffer = buffer_geom.exterior.coords.xy
    old_taz_buffer_source.data = {
        "xs": [list(xs_buffer)],
        "ys": [list(ys_buffer)],
        "id": [str(old_id_int)]
    }

    neighbors = gdf_old_taz[gdf_old_taz.intersects(buffer_geom)].copy()
    neighbors_temp = split_multipolygons_to_cds(neighbors, "taz_id")
    old_taz_neighbors_source.data = dict(neighbors_temp.data)

    old_temp = split_multipolygons_to_cds(old_sub, "taz_id")
    new_temp = split_multipolygons_to_cds(new_sub, "taz_id", 
                                          ["HH19","PERSNS19","WORKRS19","EMP19",
                                           "HH49","PERSNS49","WORKRS49","EMP49"])
    blocks_temp = split_multipolygons_to_cds(blocks_sub, "BLOCK_ID",
                                             ["HH19","PERSNS19","WORKRS19","EMP19",
                                              "HH49","PERSNS49","WORKRS49","EMP49"])

    old_blk_temp = split_multipolygons_to_cds(blocks_sub, "BLOCK_ID")
    new_blk_temp = split_multipolygons_to_cds(blocks_sub, "BLOCK_ID")
    comb_old_temp = old_temp
    comb_new_temp = new_temp
    comb_blk_temp = split_multipolygons_to_cds(blocks_sub, "BLOCK_ID")

    old_taz_source.data         = dict(old_temp.data)
    new_taz_source.data         = dict(new_temp.data)
    blocks_source.data          = dict(blocks_temp.data)
    old_taz_blocks_source.data  = dict(old_blk_temp.data)
    new_taz_blocks_source.data  = dict(new_blk_temp.data)
    combined_old_source.data    = dict(comb_old_temp.data)
    combined_new_source.data    = dict(comb_new_temp.data)
    combined_blocks_source.data = dict(comb_blk_temp.data)

    # Clear table selections
    new_taz_source.selected.indices = []
    blocks_source.selected.indices  = []
    new_taz_table_source.data = {}
    blocks_table_source.data  = {}
    update_new_taz_table()
    update_blocks_table()

    # Zoom p_old to bounding box of old_sub, then match all others
    minx, miny, maxx, maxy = old_sub.total_bounds
    if minx == maxx or miny == maxy:
        minx -= 1000; maxx += 1000
        miny -= 1000; maxy += 1000
    else:
        dx = maxx - minx
        dy = maxy - miny
        minx -= 0.05 * dx
        maxx += 0.05 * dx
        miny -= 0.05 * dy
        maxy += 0.05 * dy

    p_old.x_range.start = minx
    p_old.x_range.end   = maxx
    p_old.y_range.start = miny
    p_old.y_range.end   = maxy

    for p in [p_new, p_combined, p_blocks]:
        p.x_range.start = minx
        p.x_range.end   = maxx
        p.y_range.start = miny
        p.y_range.end   = maxy

def on_search_click():
    run_search()

search_button.on_click(on_search_click)

def on_text_input_change(attr, old, new):
    # Press Enter => run_search
    run_search()

text_input.on_change("value", on_text_input_change)

def on_tile_select_change(attr, old, new):
    if new == "CartoDB Positron":
        provider = CARTODBPOSITRON
    else:
        provider = ESRI_IMAGERY
    for fig in [p_old, p_new, p_combined, p_blocks]:
        old_tile = tile_map.get(fig)
        if old_tile and old_tile in fig.renderers:
            fig.renderers.remove(old_tile)
        new_t = fig.add_tile(provider)
        tile_map[fig] = new_t

tile_select.on_change("value", on_tile_select_change)

def on_match_zoom_click():
    # Copy p_old's range to the other panels
    for p in [p_new, p_combined, p_blocks]:
        p.x_range.start = p_old.x_range.start
        p.x_range.end   = p_old.x_range.end
        p.y_range.start = p_old.y_range.start
        p.y_range.end   = p_old.y_range.end

match_zoom_btn.on_click(on_match_zoom_click)

# -----------------------------------------------------------------------------
# 8) Layout
# -----------------------------------------------------------------------------
# Top row: left (Enter TAZ, text box), right (Selected BG)
row1_left = row(label_taz, text_input, Spacer(width=20))
row1_right = row(tile_label, tile_select)
row1 = row(row1_left, Spacer(width=50), row1_right, sizing_mode="stretch_width")

# Second row: "Search TAZ" + "Match 1st Panel Zoom"
row2 = row(search_button, match_zoom_btn, sizing_mode="stretch_width")

# Four maps (2×2) with external titles
top_maps = row(
    column(div_old_title, p_old, sizing_mode="scale_both"),
    column(div_new_title, p_new, sizing_mode="scale_both"),
    sizing_mode="scale_both"
)
bot_maps = row(
    column(div_combined_title, p_combined, sizing_mode="scale_both"),
    column(div_blocks_title, p_blocks, sizing_mode="scale_both"),
    sizing_mode="scale_both"
)
maps_col = column(top_maps, bot_maps, sizing_mode="stretch_both")

# Two tables stacked
top_table = column(Div(text="<b>New TAZ Table</b>"), new_taz_data_table, sizing_mode="scale_both")
bot_table = column(Div(text="<b>Blocks Table</b>"),  blocks_data_table,   sizing_mode="scale_both")

tables_col = column(
    top_table,
    Spacer(height=20),
    bot_table,
    width_policy="max",
    sizing_mode="stretch_both",
    min_width=100,
    max_width=375
)

main_row = row(maps_col, tables_col, sizing_mode="stretch_both")

layout_final = column(
    row1,
    row2,
    main_row,
    sizing_mode="stretch_both"
)

curdoc().add_root(layout_final)
curdoc().title = "VizTAZ - No Reset, No TAZ Label"
