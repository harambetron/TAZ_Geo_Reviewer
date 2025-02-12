"""
viztaz_app.py

---------------------------------
bokeh serve --show viztaz_app.py
---------------------------------

Features (updated):
 - Four map panels (old, new, combined, blocks) using fixed sizes (600×400) with match_aspect for calibrated hit‐testing.
 - In the top–left (old TAZ) panel all TAZ ID labels (both main and extra) are drawn in red.
 - The “Currently Searching TAZ:” label is bold.
 - The extra TAZ search controls appear immediately to the right of the “Currently Searching” label.
 - The “Enter Old TAZ ID:” controls appear on the left.
 - On the far right the map background selector and changeable buffer–radius controls appear.
 - All buttons on the top row are styled green.
 - In the second row the “Open TAZ in Google Maps” button appears to the left, then the “Match 1st Panel Zoom” button, and now a new Reset Views button (with button_type="success") appears to the right.
 - The datatable now appears in its own column immediately to the right of the maps.
 - The zoom/synchronization logic has been updated and the Reset Views button now simply calls each plot’s built‑in reset (as if you clicked the toolbar’s reset button).
"""

import os, glob
import geopandas as gpd
from shapely.geometry import Polygon, MultiPolygon
import numpy as np

from bokeh.io import curdoc
from bokeh.layouts import column, row, Spacer
from bokeh.models import (
    ColumnDataSource, DataTable, TableColumn,
    Div, TextInput, Button, Select, HoverTool, CustomJS, NumeralTickFormatter
)
from bokeh.models.widgets.tables import HTMLTemplateFormatter
from bokeh.plotting import figure
from bokeh.tile_providers import CARTODBPOSITRON, ESRI_IMAGERY

# -----------------------------------------------------------------------------
# 1. Read Shapefiles from Respective Folders
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
        return (minx == 0 and miny == 0 and maxx == 0 and maxy == 0)
    return gdf[~gdf.geometry.apply(is_zero_bbox)].copy()

gdf_old_taz  = remove_zero_geoms(gdf_old_taz)
gdf_new_taz  = remove_zero_geoms(gdf_new_taz)
gdf_blocks   = remove_zero_geoms(gdf_blocks)

# Convert to EPSG:3857 for tile providers
if gdf_old_taz.crs is None or gdf_old_taz.crs.to_string() != "EPSG:3857":
    gdf_old_taz = gdf_old_taz.to_crs(epsg=3857)
if gdf_new_taz.crs is None or gdf_new_taz.crs.to_string() != "EPSG:3857":
    gdf_new_taz = gdf_new_taz.to_crs(epsg=3857)
if gdf_blocks.crs is None or gdf_blocks.crs.to_string() != "EPSG:3857":
    gdf_blocks = gdf_blocks.to_crs(epsg=3857)

if 'taz_id' not in gdf_old_taz.columns:
    if 'TAZ_ID' in gdf_old_taz.columns:
        gdf_old_taz = gdf_old_taz.rename(columns={'TAZ_ID': 'taz_id'})

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
    gdf_blocks = gdf_blocks.rename(columns={'GEOID20': 'BLOCK_ID'})

# -----------------------------------------------------------------------------
# 2. Helper Functions
# -----------------------------------------------------------------------------
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
        if geom is None or geom.is_empty:
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

def split_multipolygons_to_text(gdf, id_field):
    """
    Returns a dictionary with centroid coordinates (cx, cy) and the id.
    Used to place text labels.
    """
    all_cx, all_cy, all_ids = [], [], []
    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        row_id = str(row[id_field])
        if geom.geom_type == "MultiPolygon":
            for subpoly in geom.geoms:
                centroid = subpoly.centroid
                all_cx.append(centroid.x)
                all_cy.append(centroid.y)
                all_ids.append(row_id)
        elif geom.geom_type == "Polygon":
            centroid = geom.centroid
            all_cx.append(centroid.x)
            all_cy.append(centroid.y)
            all_ids.append(row_id)
    return {"cx": all_cx, "cy": all_cy, "id": all_ids}

def add_sum_row(d, colnames):
    if 'id' not in d:
        d['id'] = []
        for c in colnames:
            d[c] = []
    sums = {c: 0 for c in colnames}
    for c in colnames:
        for val in d[c]:
            if isinstance(val, (int, float)):
                sums[c] += val
    d['id'].append("Sum")
    for c in colnames:
        d[c].append(sums[c])
    return d

def add_formatted_fields(source, fields):
    for field in fields:
        fmt_field = field + "_fmt"
        if field in source.data:
            source.data[fmt_field] = [f"{x:.1f}" if isinstance(x, (int, float)) else "" for x in source.data[field]]

# -----------------------------------------------------------------------------
# 3. DataSources for Panels, Text Labels, and Centroid (for Google Maps)
# -----------------------------------------------------------------------------
old_taz_source        = ColumnDataSource(dict(xs=[], ys=[], id=[]))
old_taz_blocks_source = ColumnDataSource(dict(xs=[], ys=[], id=[]))
new_taz_source = ColumnDataSource(dict(xs=[], ys=[], id=[], 
                                       HH19=[], PERSNS19=[], WORKRS19=[], EMP19=[], 
                                       HH49=[], PERSNS49=[], WORKRS49=[], EMP49=[]))
new_taz_blocks_source = ColumnDataSource(dict(xs=[], ys=[], id=[]))
blocks_source  = ColumnDataSource(dict(xs=[], ys=[], id=[], 
                                        HH19=[], PERSNS19=[], WORKRS19=[], EMP19=[], 
                                        HH49=[], PERSNS49=[], WORKRS49=[], EMP49=[]))
combined_old_source    = ColumnDataSource(dict(xs=[], ys=[], id=[]))
combined_new_source    = ColumnDataSource(dict(xs=[], ys=[], id=[], 
                                               HH19=[], PERSNS19=[], WORKRS19=[], EMP19=[], 
                                               HH49=[], PERSNS49=[], WORKRS49=[], EMP49=[]))
combined_blocks_source = ColumnDataSource(dict(xs=[], ys=[], id=[]))
old_taz_buffer_source = ColumnDataSource(dict(xs=[], ys=[], id=[]))
old_taz_neighbors_source = ColumnDataSource(dict(xs=[], ys=[], id=[]))
extra_old_taz_source = ColumnDataSource(dict(xs=[], ys=[], id=[]))
old_taz_text_source = ColumnDataSource(dict(cx=[], cy=[], id=[]))
new_taz_text_source = ColumnDataSource(dict(cx=[], cy=[], id=[]))
extra_old_taz_text_source = ColumnDataSource(dict(cx=[], cy=[], id=[]))
centroid_source = ColumnDataSource(data={'cx': [], 'cy': []})

global_new_gdf    = None
global_blocks_gdf = None

# -----------------------------------------------------------------------------
# 4. Figures – Fixed sizes (600×400) with match_aspect=True
# -----------------------------------------------------------------------------
TOOLS = "pan,wheel_zoom,box_zoom,reset"

p_old = figure(width=600, height=400, x_axis_type="mercator", y_axis_type="mercator",
               tools=TOOLS, active_scroll='wheel_zoom', title=None)
p_old.match_aspect = True

p_new = figure(width=600, height=400, x_axis_type="mercator", y_axis_type="mercator",
               tools=TOOLS + ",tap", active_scroll='wheel_zoom', title=None)
p_new.match_aspect = True

p_combined = figure(width=600, height=400, x_axis_type="mercator", y_axis_type="mercator",
                    tools=TOOLS, active_scroll='wheel_zoom', title=None)
p_combined.match_aspect = True

p_blocks = figure(width=600, height=400, x_axis_type="mercator", y_axis_type="mercator",
                  tools=TOOLS + ",tap", active_scroll='wheel_zoom', title=None)
p_blocks.match_aspect = True

div_old_title      = Div(text="<b>1) Old TAZ (red IDs)</b>", styles={'font-size': '16px'})
div_new_title      = Div(text="<b>2) New TAZ (red; blocks not selectable)</b>", styles={'font-size': '16px'})
div_combined_title = Div(text="<b>3) Combined (new=red, old=red, blocks=yellow)</b>", styles={'font-size': '16px'})
div_blocks_title   = Div(text="<b>4) Blocks (selectable, yellow)</b>", styles={'font-size': '16px'})

tile_map = {}
def add_tiles():
    for fig in [p_old, p_new, p_combined, p_blocks]:
        tile = fig.add_tile(CARTODBPOSITRON)
        tile_map[fig] = tile
add_tiles()

for fig in [p_old, p_new, p_combined, p_blocks]:
    fig.xaxis.formatter = NumeralTickFormatter(format="0.2~f")
    fig.yaxis.formatter = NumeralTickFormatter(format="0.2~f")

# -----------------------------------------------------------------------------
# 5. Patch Glyphs (map features)
# -----------------------------------------------------------------------------
renderer_old_taz = p_old.patches(xs="xs", ys="ys", source=old_taz_source,
                                 fill_color="lightgreen", fill_alpha=0.3,
                                 line_color="green", line_width=2)
p_old.patches(xs="xs", ys="ys", source=old_taz_blocks_source,
              fill_color=None, line_color="black", line_width=2, line_dash='dotted')
old_taz_buffer_renderer = p_old.patches(xs="xs", ys="ys", source=old_taz_buffer_source,
                                        fill_color="lightcoral", fill_alpha=0.3, line_color=None)
p_old.renderers.remove(old_taz_buffer_renderer)
p_old.renderers.insert(0, old_taz_buffer_renderer)
old_taz_neighbors_renderer = p_old.patches(xs="xs", ys="ys", source=old_taz_neighbors_source,
                                           fill_color=None, line_color="gray", line_width=2, line_dash="dotted")
p_old.renderers.remove(old_taz_neighbors_renderer)
p_old.renderers.insert(1, old_taz_neighbors_renderer)
renderer_extra_old_taz = p_old.patches(xs="xs", ys="ys", source=extra_old_taz_source,
                                       fill_color="#E6E6FA", fill_alpha=0.3,
                                       line_color="purple", line_width=2)
hover_old_patches = HoverTool(tooltips=[("Old TAZ ID", "@id")],
                              renderers=[renderer_old_taz, renderer_extra_old_taz])
p_old.add_tools(hover_old_patches)

taz_glyph_new = p_new.patches(xs="xs", ys="ys", source=new_taz_source,
                              fill_color=None, line_color="red", line_width=2,
                              selection_fill_color="yellow", selection_fill_alpha=0.3,
                              selection_line_color="red", selection_line_width=2,
                              nonselection_fill_color=None, nonselection_line_color="red")
p_new.patches(xs="xs", ys="ys", source=new_taz_blocks_source,
              fill_color=None, line_color="black", line_width=2, line_dash='dotted')
p_new.add_tools(HoverTool(
    tooltips=[
        ("TAZ ID", "@id"),
        ("HH19", "@HH19_fmt"),
        ("EMP19", "@EMP19_fmt"),
        ("HH49", "@HH49_fmt"),
        ("EMP49", "@EMP49_fmt")
    ],
    renderers=[taz_glyph_new]
))

p_combined.patches(xs="xs", ys="ys", source=combined_new_source,
                   fill_color=None, line_color="red", line_width=2)
p_combined.patches(xs="xs", ys="ys", source=combined_old_source,
                   fill_color=None, line_color="green", line_width=2)
p_combined.patches(xs="xs", ys="ys", source=combined_blocks_source,
                   fill_color="yellow", fill_alpha=0.3,
                   line_color="black", line_width=2, line_dash='dotted')
p_combined.patches(xs="xs", ys="ys", source=extra_old_taz_source,
                   fill_color="#E6E6FA", fill_alpha=0.3,
                   line_color="purple", line_width=2)

renderer_blocks = p_blocks.patches(xs="xs", ys="ys", source=blocks_source,
                                     fill_color="yellow", fill_alpha=0.3,
                                     line_color="black", line_width=2, line_dash='dotted', line_alpha=0.85,
                                     selection_fill_color="yellow", selection_fill_alpha=0.3,
                                     selection_line_color="black", selection_line_dash='dotted',
                                     nonselection_fill_alpha=0.10, nonselection_line_color="black",
                                     nonselection_line_dash='dotted', nonselection_line_alpha=0.85)
p_blocks.add_tools(HoverTool(
    tooltips=[
        ("Block ID", "@id"),
        ("HH19", "@HH19_fmt"),
        ("EMP19", "@EMP19_fmt"),
        ("HH49", "@HH49_fmt"),
        ("EMP49", "@EMP49_fmt")
    ],
    renderers=[renderer_blocks]
))

p_old.text(x="cx", y="cy", text="id", source=old_taz_text_source,
           text_color="red", text_font_size="10pt", text_font_style="bold",
           text_align="center", text_baseline="middle")
p_old.text(x="cx", y="cy", text="id", source=extra_old_taz_text_source,
           text_color="red", text_font_size="10pt", text_font_style="bold",
           text_align="center", text_baseline="middle")
p_new.text(x="cx", y="cy", text="id", source=new_taz_text_source,
           text_color="red", text_font_size="10pt", text_font_style="bold",
           text_align="center", text_baseline="middle")

# -----------------------------------------------------------------------------
# 6. Tables with Persistent Sum Row
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

new_taz_data_table = DataTable(source=new_taz_table_source, columns=table_cols, width=550, height=300, fit_columns=False)
blocks_data_table  = DataTable(source=blocks_table_source,  columns=table_cols, width=550, height=300, fit_columns=False)

def update_new_taz_table():
    inds = new_taz_source.selected.indices
    d = {"id":[], "HH19":[], "PERSNS19":[], "WORKRS19":[], "EMP19":[],
         "HH49":[], "PERSNS49":[], "WORKRS49":[], "EMP49":[]}
    if inds:
        for c in d.keys():
            if c in new_taz_source.data:
                d[c] = [new_taz_source.data[c][i] for i in inds]
    d = add_sum_row(d, ["HH19","PERSNS19","WORKRS19","EMP19", "HH49","PERSNS49","WORKRS49","EMP49"])
    new_taz_table_source.data = d

def update_blocks_table():
    inds = blocks_source.selected.indices
    d = {"id":[], "HH19":[], "PERSNS19":[], "WORKRS19":[], "EMP19":[],
         "HH49":[], "PERSNS49":[], "WORKRS49":[], "EMP49":[]}
    if inds:
        for c in d.keys():
            d[c] = [blocks_source.data[c][i] for i in inds]
    d = add_sum_row(d, ["HH19","PERSNS19","WORKRS19","EMP19", "HH49","PERSNS49","WORKRS49","EMP49"])
    blocks_table_source.data = d

new_taz_source.selected.on_change("indices", lambda attr, old, new: update_new_taz_table())
blocks_source.selected.on_change("indices", lambda attr, old, new: update_blocks_table())

# -----------------------------------------------------------------------------
# 7. UI Elements – Updated Layout and Green Button Styles
# -----------------------------------------------------------------------------
curdoc().add_root(Div(text="""
<style>
.my-green-button .bk-btn {
    background-color: green !important;
    color: white !important;
}
</style>
""", visible=False))

# Top row input controls (all buttons here are green).
search_label = Div(text="<b>Currently Searching TAZ: <span style='color:red'>(none)</span></b>", width=300)
extra_taz_label = Div(text="<b>Extra TAZ IDs (comma separated):</b>", width=200)
extra_taz_input = TextInput(value="", title="", placeholder="e.g. 101, 102, 103", width=150)
extra_search_button = Button(label="Search Extra TAZ", width=120)
extra_search_button.css_classes.append("my-green-button")
label_taz = Div(text="<b>Enter Old TAZ ID:</b>", width=120)
text_input = TextInput(value="", title="", placeholder="TAZ ID...", width=100)
search_button = Button(label="Search TAZ", width=80)
search_button.css_classes.append("my-green-button")
tile_label = Div(text="<b>Selected Map Background:</b>", width=150)
tile_select = Select(value="CartoDB Positron", options=["CartoDB Positron","ESRI Satellite"], width=140)
radius_label = Div(text="<b>Buffer Radius (m):</b>", width=150)
radius_input = TextInput(value="1000", title="", placeholder="e.g. 1000", width=80)
apply_radius_button = Button(label="Apply Radius", width=100)
apply_radius_button.css_classes.append("my-green-button")

group_left   = row(label_taz, text_input, search_button)
group_center = row(search_label)
group_extra  = row(extra_taz_label, extra_taz_input, extra_search_button)
group_right  = row(tile_label, tile_select, radius_label, radius_input, apply_radius_button)
row1_combined = row(group_left, Spacer(width=10), group_center, Spacer(width=10), group_extra, Spacer(width=10), group_right)

# Second row buttons.
open_gmaps_button = Button(label="Open TAZ in Google Maps", button_type="warning", width=150)
open_gmaps_button.js_on_click(CustomJS(args=dict(centroid_source=centroid_source), code="""
    var data = centroid_source.data;
    if (data['cx'].length === 0 || data['cy'].length === 0) {
        alert("No TAZ centroid available. Please perform a search first.");
        return;
    }
    var x = data['cx'][0];
    var y = data['cy'][0];
    var R = 6378137.0;
    var lon = (x / R) * (180 / Math.PI);
    var lat = (Math.PI / 2 - 2 * Math.atan(Math.exp(-y / R))) * (180 / Math.PI);
    var url = "https://www.google.com/maps?q=" + lat + "," + lon;
    window.open(url, "_blank");
"""))
match_zoom_btn = Button(label="Match 1st Panel Zoom", button_type="primary", width=130)
# NEW Reset Views button now uses Bokeh's reset by emitting each plot's reset event.
reset_btn = Button(label="Reset Views", button_type="success", width=130)
def reset_views():
    for p in [p_old, p_new, p_combined, p_blocks]:
        p.reset.emit()
reset_btn.on_click(reset_views)

row2 = row(open_gmaps_button, Spacer(width=10), match_zoom_btn, Spacer(width=10), reset_btn)

# -----------------------------------------------------------------------------
# 8. Main Search Function – Updated Synchronization Logic
# -----------------------------------------------------------------------------
def run_search():
    val = text_input.value.strip()
    if not val:
        search_label.text = "<b>Currently Searching TAZ: <span style='color:red'>(no input)</span></b>"
        return
    try:
        old_id_int = int(val)
    except ValueError:
        search_label.text = "<b>Currently Searching TAZ: <span style='color:red'>[TAZ Not Found]</span></b>"
        return
    try:
        radius = float(radius_input.value.strip())
        if radius <= 0:
            radius = 1000
    except ValueError:
        radius = 1000
        radius_input.value = "1000"
    
    subset_old = gdf_old_taz[gdf_old_taz['taz_id'] == old_id_int]
    if subset_old.empty:
        search_label.text = "<b>Currently Searching TAZ: <span style='color:red'>[TAZ Not Found]</span></b>"
        return

    search_label.text = f"<b>Currently Searching TAZ: <span style='color:red'>{old_id_int}</span></b>"
    global global_new_gdf, global_blocks_gdf
    old_union = subset_old.unary_union
    centroid = old_union.centroid
    centroid_source.data = {"cx": [centroid.x], "cy": [centroid.y]}
    
    buffer_geom = centroid.buffer(radius)
    xs_buffer, ys_buffer = buffer_geom.exterior.coords.xy
    old_taz_buffer_source.data = {
        "xs": [list(xs_buffer)],
        "ys": [list(ys_buffer)],
        "id": [str(old_id_int)]
    }
    
    neighbors = gdf_old_taz[gdf_old_taz.intersects(buffer_geom)].copy()
    neighbors_temp = split_multipolygons_to_cds(neighbors, "taz_id")
    old_taz_neighbors_source.data = dict(neighbors_temp.data)
    
    new_sub = gdf_new_taz[gdf_new_taz.intersects(buffer_geom)].copy()
    blocks_sub = gdf_blocks[gdf_blocks.intersects(buffer_geom)].copy()
    
    old_temp = split_multipolygons_to_cds(subset_old, "taz_id")
    new_temp = split_multipolygons_to_cds(new_sub, "taz_id", 
                                          ["HH19", "PERSNS19", "WORKRS19", "EMP19",
                                           "HH49", "PERSNS49", "WORKRS49", "EMP49"])
    blocks_temp = split_multipolygons_to_cds(blocks_sub, "BLOCK_ID", 
                                             ["HH19", "PERSNS19", "WORKRS19", "EMP19",
                                              "HH49", "PERSNS49", "WORKRS49", "EMP49"])
    old_blocks_temp = split_multipolygons_to_cds(blocks_sub, "BLOCK_ID")
    new_blocks_temp = split_multipolygons_to_cds(blocks_sub, "BLOCK_ID")
    comb_old_temp   = old_temp
    comb_new_temp   = new_temp
    comb_blocks_temp = split_multipolygons_to_cds(blocks_sub, "BLOCK_ID")
    
    add_formatted_fields(new_temp, ["HH19", "EMP19", "HH49", "EMP49"])
    add_formatted_fields(blocks_temp, ["HH19", "EMP19", "HH49", "EMP49"])
    
    old_taz_source.data         = dict(old_temp.data)
    new_taz_source.data         = dict(new_temp.data)
    blocks_source.data          = dict(blocks_temp.data)
    old_taz_blocks_source.data  = dict(old_blocks_temp.data)
    new_taz_blocks_source.data  = dict(new_blocks_temp.data)
    combined_old_source.data    = dict(comb_old_temp.data)
    combined_new_source.data    = dict(comb_new_temp.data)
    combined_blocks_source.data = dict(comb_blocks_temp.data)
    
    old_taz_text_source.data = split_multipolygons_to_text(subset_old, "taz_id")
    new_taz_text_source.data = split_multipolygons_to_text(new_sub, "taz_id")
    
    new_taz_source.selected.indices = []
    blocks_source.selected.indices  = []
    new_taz_table_source.data = dict()
    blocks_table_source.data  = dict()
    update_new_taz_table()
    update_blocks_table()
    
    minx, miny, maxx, maxy = subset_old.total_bounds
    if minx == maxx or miny == maxy:
        minx -= 1000; maxx += 1000; miny -= 1000; maxy += 1000
    else:
        dx = maxx - minx; dy = maxy - miny
        minx -= 0.05 * dx; maxx += 0.05 * dx; miny -= 0.05 * dy; maxy += 0.05 * dy

    p_old.x_range.start = minx
    p_old.x_range.end   = maxx
    p_old.y_range.start = miny
    p_old.y_range.end   = maxy
    for p in [p_new, p_combined, p_blocks]:
        p.x_range.start = minx
        p.x_range.end   = maxx
        p.y_range.start = miny
        p.y_range.end   = maxy

    calibrate_plots()

def on_search_click():
    run_search()

search_button.on_click(on_search_click)
text_input.on_change("value", lambda attr, old, new: run_search())
apply_radius_button.on_click(lambda: run_search())

def on_tile_select_change(attr, old, new):
    if new == "CartoDB Positron":
        provider = CARTODBPOSITRON
    else:
        provider = ESRI_IMAGERY
    for fig in [p_old, p_new, p_combined, p_blocks]:
        old_tile = tile_map.get(fig)
        if old_tile and old_tile in fig.renderers:
            fig.renderers.remove(old_tile)
        new_tile = fig.add_tile(provider)
        tile_map[fig] = new_tile

tile_select.on_change("value", on_tile_select_change)

def on_match_zoom_click():
    for p in [p_new, p_combined, p_blocks]:
        p.x_range.start = p_old.x_range.start
        p.x_range.end   = p_old.x_range.end
        p.y_range.start = p_old.y_range.start
        p.y_range.end   = p_old.y_range.end

match_zoom_btn.on_click(on_match_zoom_click)

def run_extra_search():
    val = extra_taz_input.value.strip()
    if not val:
        extra_old_taz_source.data = {"xs": [], "ys": [], "id": []}
        extra_old_taz_text_source.data = {"cx": [], "cy": [], "id": []}
        return
    try:
        id_list = [int(x.strip()) for x in val.split(",") if x.strip() != ""]
    except ValueError:
        extra_old_taz_source.data = {"xs": [], "ys": [], "id": []}
        extra_old_taz_text_source.data = {"cx": [], "cy": [], "id": []}
        return
    subset_extra = gdf_old_taz[gdf_old_taz['taz_id'].isin(id_list)]
    if subset_extra.empty:
        extra_old_taz_source.data = {"xs": [], "ys": [], "id": []}
        extra_old_taz_text_source.data = {"cx": [], "cy": [], "id": []}
        return
    extra_cdsrc = split_multipolygons_to_cds(subset_extra, "taz_id")
    extra_old_taz_source.data = dict(extra_cdsrc.data)
    extra_text_data = split_multipolygons_to_text(subset_extra, "taz_id")
    extra_old_taz_text_source.data = extra_text_data

extra_search_button.on_click(run_extra_search)
extra_taz_input.on_change("value_input", lambda attr, old, new: run_extra_search())
extra_taz_input.on_event("value_submit", lambda event: run_extra_search())

# -----------------------------------------------------------------------------
# 9. Layout – Place maps and datatable in adjacent columns
# -----------------------------------------------------------------------------
group_left   = row(label_taz, text_input, search_button)
group_center = row(search_label)
group_extra  = row(extra_taz_label, extra_taz_input, extra_search_button)
group_right  = row(tile_label, tile_select, radius_label, radius_input, apply_radius_button)
row1_combined = row(group_left, Spacer(width=10), group_center, Spacer(width=10), group_extra, Spacer(width=10), group_right)
row2 = row(open_gmaps_button, Spacer(width=10), match_zoom_btn, Spacer(width=10), reset_btn)

top_maps = row(column(div_old_title, p_old),
               column(div_new_title, p_new))
bot_maps = row(column(div_combined_title, p_combined),
               column(div_blocks_title, p_blocks))
maps_col = column(top_maps, bot_maps, sizing_mode="stretch_both")
tables_col = column(column(Div(text="<b>New TAZ Table</b>"), new_taz_data_table),
                     column(Div(text="<b>Blocks Table</b>"), blocks_data_table),
                     width=400, spacing=10, sizing_mode="fixed")
main_row = row(maps_col, Spacer(width=20), tables_col, sizing_mode="stretch_both")
layout_final = column(row1_combined, row2, main_row, sizing_mode="stretch_both")

curdoc().add_root(layout_final)
curdoc().title = "Final Layout - Maps & Tables Adjacent, Green Buttons, Reset Views"

# -----------------------------------------------------------------------------
# 10. Calibration Callback – Re-emit range values.
# -----------------------------------------------------------------------------
from tornado.ioloop import IOLoop
def calibrate_plots():
    for plot in [p_old, p_new, p_combined, p_blocks]:
        plot.x_range.start = plot.x_range.start
        plot.x_range.end   = plot.x_range.end
        plot.y_range.start = plot.y_range.start
        plot.y_range.end   = plot.y_range.end
curdoc().add_next_tick_callback(calibrate_plots)
