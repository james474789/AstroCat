# Metadata Search & Viewer - Implementation Guide

## 🎯 Overview

AstroCat now features two powerful metadata tools:

### 1. **Metadata Search** (`/metadata-search`)
A comprehensive search and filtering interface for exploring your image collection with advanced criteria.

### 2. **Metadata Viewer** (`/images/:id/metadata`)
A detailed metadata page for individual images with multiple viewing formats.

---

## 📍 Features

### Metadata Search Page

#### Advanced Filtering
- **Quick Search**: Search across file names and object names
- **FITS/EXIF Headers**: Search by header key and value
- **Equipment Filters**: Filter by camera, telescope, and filter
- **Exposure Ranges**: Set min/max for exposure time and gain
- **Plate Solve Status**: Filter solved vs. unsolved images
- **Classification**: Filter by image type (Sub Frame, Integration Master, etc.)
- **Date Range**: Filter images by capture date range
- **Object Name**: Filter by astronomical object (M31, NGC7000, etc.)

#### Results Display
- **Rich Table View**: Shows file format, object name, camera, telescope, exposure, date, plate solve status, and rating
- **Multi-Select**: Checkbox selection for batch operations
- **Bulk Export**: Download selected metadata as CSV
- **Quick View**: Click eye icon to view full metadata for any image

#### User Experience
- **Collapsible Filters**: Sidebar can be collapsed on smaller screens
- **Live Results**: Results update as you adjust filters
- **Result Count**: Shows total matching images and number selected
- **Responsive Design**: Works seamlessly on mobile, tablet, and desktop
- **Reset Filters**: One-click button to clear all filters

---

### Metadata Viewer Page

#### Four Viewing Modes

**1. Summary Tab (📋)**
- Curated list of most important metadata
- Organized by category (File Info, Observational Data, Equipment, etc.)
- Copy-to-clipboard for each value
- Shows only relevant (non-null) fields

**2. Details Tab (📊)**
- All metadata organized in expandable sections:
  - File Information
  - Observational Data
  - Plate Solving & WCS
  - Exposure & Imaging Settings
  - Equipment
  - Quality & Classification
  - Astrometry.net Status
- Collapse/expand sections for clean UI
- Copy buttons on every field

**3. Raw Headers Tab (🔍)**
- Full FITS/EXIF header browser
- Real-time search across keys and values
- Match counter showing filtered vs. total headers
- Syntax-highlighted code display

**4. Export Tab (💾)**
- Download metadata in multiple formats:
  - **JSON**: Structured, API-friendly format
  - **CSV**: Spreadsheet-ready for comparison
  - **Text**: Human-readable documentation
- Copy to clipboard option for each format
- Live JSON preview

#### Quick Reference Footer
- File size, dimensions, exposure time, and camera displayed at bottom
- Always accessible when scrolling through metadata

---

## 🗺️ Navigation

### Updated Navigation Menu
The main sidebar navigation now includes:
- **Metadata Search** (formerly "Metadata Explore")
- **Metadata Viewer** (linked from image detail pages and search results)

### Access Points
1. **From Image Detail**: Click "📋 View Metadata" button
2. **From Metadata Search**: Click eye icon in results table
3. **From Search Results**: Click metadata button on any image
4. **Direct URL**: `/metadata-search` or `/images/{id}/metadata`

---

## 🔧 API Compatibility

The metadata search uses the same API endpoint as the standard image search but with additional filter parameters:

### Supported Query Parameters
```
GET /api/images/
├── search (text search)
├── header_key (FITS key)
├── header_value (FITS value)
├── camera_name
├── telescope_name
├── filter_name
├── exposure_min / exposure_max
├── gain_min / gain_max
├── is_plate_solved (boolean)
├── subtype
├── date_from / date_to
├── object_name
└── page, page_size
```

---

## 💡 Use Cases

### Finding Similar Images
1. Open any image's metadata viewer
2. Note key characteristics (exposure time, camera, filter, etc.)
3. Go to Metadata Search
4. Set matching filters
5. Browse results

### Comparing Equipment
1. Go to Metadata Search
2. Filter by specific camera and telescope
3. Export selected results as CSV
4. Open in spreadsheet to analyze exposure settings

### Quality Analysis
1. Search for plate-solved images only
2. Sort by plate solve provider or date
3. Review exposure settings of successful images
4. Export for further analysis

### Archival Search
1. Use date range filters
2. Filter by object name
3. Find all images of specific targets
4. Bulk export metadata for records

---

## 🎨 UI/UX Highlights

- **Dark theme** consistent with AstroCat design
- **Color-coded badges** for file formats and plate solve status
- **Responsive design** that works on all screen sizes
- **Sticky table headers** for easy reference while scrolling
- **Smooth animations** and transitions
- **Accessible keyboard navigation**
- **Copy-to-clipboard** feedback with visual confirmation
- **Expandable sections** to manage information density

---

## 📊 Performance Notes

- **Lazy loading** of raw headers (only fetched when needed)
- **Efficient pagination** with 25 results per page
- **Debounced search** to minimize API calls
- **Sticky positioning** for filters and headers
- **Virtual scrolling** consideration for large result sets

---

## 🚀 Future Enhancement Ideas

1. **Saved Searches**: Save frequently used filter combinations
2. **Advanced Statistics**: Show distribution graphs of metadata values
3. **Image Comparison**: Side-by-side comparison of multiple images
4. **Smart Recommendations**: Suggest filter combinations based on usage
5. **Custom Columns**: Allow users to configure which metadata columns to display
6. **Sorting**: Click column headers to sort results
7. **Export Formats**: Additional export options (JSON Lines, XML)
8. **Metadata Diff**: Compare metadata between images
9. **Trending**: Show most common metadata values
10. **Integration**: Link search results to other tools

---

## Files Modified/Created

### New Files
- `frontend/src/pages/MetadataSearch.jsx` - Main search component
- `frontend/src/pages/MetadataSearch.css` - Search page styling
- `frontend/src/pages/MetadataViewer.jsx` - Detail viewer component
- `frontend/src/pages/MetadataViewer.css` - Viewer page styling
- `frontend/src/components/metadata/MetadataSummaryTab.jsx` - Summary view
- `frontend/src/components/metadata/MetadataDetailsTab.jsx` - Detailed view
- `frontend/src/components/metadata/MetadataRawTab.jsx` - Raw headers view
- `frontend/src/components/metadata/MetadataExportTab.jsx` - Export view
- `frontend/src/components/metadata/MetadataTab.css` - Shared tab styling
- `frontend/src/components/metadata/index.js` - Component exports

### Modified Files
- `frontend/src/App.jsx` - Updated routes
- `frontend/src/pages/ImageDetail.jsx` - Updated metadata button
- `frontend/src/components/layout/Layout.jsx` - Updated navigation menu

---

## ✅ Testing Checklist

- [ ] Metadata Search page loads and displays images
- [ ] Filters work individually and in combination
- [ ] Reset Filters button clears all selections
- [ ] Pagination works correctly
- [ ] Select All checkbox works
- [ ] CSV export includes correct columns
- [ ] Metadata Viewer loads for any image
- [ ] All four tabs display correctly
- [ ] Copy-to-clipboard works
- [ ] Raw headers search filters results
- [ ] Export formats generate correct content
- [ ] Page is responsive on mobile devices
- [ ] Sidebar collapses/expands on small screens
- [ ] Navigation menu updated correctly
