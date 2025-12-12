/**
 * Recipe Import Material Creator
 * Handles inline creation of suppliers and materials during recipe import
 */

class RecipeImportMaterialCreator {
    constructor() {
        this.newSuppliers = [];
        this.createdMaterials = {};
        this.pendingSuppliers = new Set();
        this.materialStates = new Map(); // Track state of each material
        this.suppliersNeedingCreation = [];
        this.categories = [];
        this.suppliers = [];
        this.errors = [];
        this.currentSupplierSelect = null; // Track which select triggered new supplier
        this.init();
    }

    init() {
        // Load initial data
        this.loadCategories();
        this.loadSuppliers();

        // Scan for materials needing suppliers
        this.identifyMissingSuppliersAndMaterials();

        // Setup event handlers
        this.setupEventHandlers();

        // Initialize material states
        this.initializeMaterialStates();
    }

    initializeMaterialStates() {
        $('.material-row').each((index, row) => {
            const $row = $(row);
            const recipeIdx = $row.data('recipe-idx');
            const materialIdx = $row.data('material-idx');
            const key = `${recipeIdx}_${materialIdx}`;

            // Check if material already exists (has mapping dropdown)
            const hasMapping = $row.find('.material-mapping-select').length > 0;

            if (hasMapping) {
                this.materialStates.set(key, 'unmapped');
            } else {
                this.materialStates.set(key, 'exists');
            }
        });
    }

    loadCategories() {
        $.get('/api/recipe_import/get_categories')
            .done((response) => {
                if (response.success) {
                    this.categories = response.categories;
                    this.updateCategoryDropdowns();
                }
            })
            .fail((error) => {
                console.error('Failed to load categories:', error);
            });
    }

    loadSuppliers() {
        $.get('/api/recipe_import/get_suppliers')
            .done((response) => {
                if (response.success) {
                    this.suppliers = response.suppliers;
                    this.updateSupplierDropdowns();
                }
            })
            .fail((error) => {
                console.error('Failed to load suppliers:', error);
            });
    }

    updateCategoryDropdowns() {
        $('.category-select').each((index, select) => {
            const $select = $(select);
            $select.empty();
            $select.append('<option value="">Select...</option>');

            this.categories.forEach(cat => {
                $select.append(`<option value="${cat.id}">${cat.name}</option>`);
            });
        });
    }

    updateSupplierDropdowns() {
        $('.supplier-select').each((index, select) => {
            const $select = $(select);
            const currentValue = $select.val();

            $select.empty();
            $select.append('<option value="">Select...</option>');
            $select.append('<option value="new">➕ New Supplier...</option>');

            this.suppliers.forEach(sup => {
                const isNew = sup.is_new ? ' 🆕' : '';
                const discount = sup.discount_percentage > 0 ? ` (-${sup.discount_percentage}%)` : '';
                $select.append(`<option value="${sup.id}">${sup.name}${isNew}${discount}</option>`);
            });

            // Restore previous selection if still valid
            if (currentValue && $select.find(`option[value="${currentValue}"]`).length > 0) {
                $select.val(currentValue);
            }
        });
    }

    identifyMissingSuppliersAndMaterials() {
        const materialsNeedingSuppliers = [];

        $('.material-row').each((index, row) => {
            const $row = $(row);
            const hasMapping = $row.find('.material-mapping-select').length > 0;

            if (hasMapping) {
                const materialName = $row.data('material-name');
                const materialType = $row.data('material-type');

                // Check if user might need a new supplier for this material
                materialsNeedingSuppliers.push({
                    name: materialName,
                    type: materialType,
                    row: $row
                });
            }
        });

        // If we have materials needing attention, show supplier creation section
        if (materialsNeedingSuppliers.length > 0) {
            this.showSupplierPrompt(materialsNeedingSuppliers);
        }
    }

    showSupplierPrompt(materials) {
        const uniqueMaterials = [...new Set(materials.map(m => m.name))];

        // Build prompt message
        let promptHtml = `
            <p>The following materials are not in the system:</p>
            <ul>
                ${uniqueMaterials.map(name => `<li>${name}</li>`).join('')}
            </ul>
            <p>Do you need to create new suppliers before adding these materials?</p>
            <button id="addSupplierBtn" class="btn btn-primary me-2">
                <i class="bi bi-plus-circle me-1"></i> Add New Supplier
            </button>
            <button id="skipSupplierBtn" class="btn btn-secondary">
                <i class="bi bi-arrow-right me-1"></i> Use Existing Suppliers
            </button>
        `;

        $('#supplierCreationContainer').html(promptHtml);
        $('#supplierCreationSection').show();
    }

    setupEventHandlers() {
        const self = this;

        // Supplier creation buttons
        $(document).on('click', '#addSupplierBtn', function() {
            self.showSupplierForm();
        });

        $(document).on('click', '#skipSupplierBtn', function() {
            $('#supplierCreationSection').hide();
        });

        // Expand/collapse material creation forms
        $(document).on('click', '.expand-create-btn', function() {
            const $btn = $(this);
            const $row = $btn.closest('.material-row');
            const $form = $row.find('.material-create-form');
            const $icon = $btn.find('i');

            if ($form.is(':visible')) {
                $form.slideUp(200);
                $icon.removeClass('bi-chevron-up').addClass('bi-chevron-down');
            } else {
                $form.slideDown(200);
                $icon.removeClass('bi-chevron-down').addClass('bi-chevron-up');
            }
        });

        // Create/Map toggle
        $(document).on('change', '.create-toggle', function() {
            const $toggle = $(this);
            const $row = $toggle.closest('.material-row');
            const $createFields = $row.find('.create-fields');
            const $mappingSelect = $row.find('.material-mapping-select');

            if ($toggle.is(':checked')) {
                // Show creation fields, hide mapping dropdown
                $createFields.slideDown(200);
                if ($mappingSelect.length) {
                    $mappingSelect.closest('.material-status-cell').find('select, .badge').hide();
                }
            } else {
                // Hide creation fields, show mapping dropdown
                $createFields.slideUp(200);
                if ($mappingSelect.length) {
                    $mappingSelect.closest('.material-status-cell').find('select, .badge').show();
                }
            }
        });

        // Supplier dropdown change
        $(document).on('change', '.supplier-select', function() {
            const $select = $(this);

            if ($select.val() === 'new') {
                // Store reference to the select that triggered this
                self.currentSupplierSelect = $select;

                // Show supplier form with callback
                self.showSupplierForm(function(newSupplierId) {
                    // After creating supplier, reload list and select the new one
                    self.loadSuppliers();
                    setTimeout(() => {
                        // Set the value on the original select that triggered this
                        if (self.currentSupplierSelect) {
                            self.currentSupplierSelect.val(newSupplierId);
                            self.currentSupplierSelect = null; // Clear reference
                        }
                    }, 500);
                });
            }
        });

        // Create material button
        $(document).on('click', '.create-material-btn', function() {
            const $btn = $(this);
            const $row = $btn.closest('.material-row');
            self.createMaterial($row, $btn);
        });

        // Cancel create button
        $(document).on('click', '.cancel-create-btn', function() {
            const $row = $(this).closest('.material-row');
            const $toggle = $row.find('.create-toggle');
            const $createFields = $row.find('.create-fields');

            $toggle.prop('checked', false);
            $createFields.slideUp(200);

            // Show mapping dropdown again
            const $mappingSelect = $row.find('.material-mapping-select');
            if ($mappingSelect.length) {
                $mappingSelect.closest('.material-status-cell').find('select, .badge').show();
            }
        });

        // Proceed to materials button
        $(document).on('click', '#proceedToMaterials', function() {
            $('#supplierCreationSection').hide();
        });
    }

    showSupplierForm(callback) {
        const formHtml = `
            <div class="card mb-3">
                <div class="card-body">
                    <h6 class="card-title">New Supplier Details</h6>
                    <div class="row g-2">
                        <div class="col-md-4">
                            <label class="form-label">Supplier Name <span class="text-danger">*</span></label>
                            <input type="text" class="form-control" id="newSupplierName" required>
                        </div>
                        <div class="col-md-4">
                            <label class="form-label">Contact Person</label>
                            <input type="text" class="form-control" id="newSupplierContact">
                        </div>
                        <div class="col-md-4">
                            <label class="form-label">Phone</label>
                            <input type="text" class="form-control" id="newSupplierPhone">
                        </div>
                    </div>
                    <div class="row g-2 mt-2">
                        <div class="col-md-4">
                            <label class="form-label">Email</label>
                            <input type="email" class="form-control" id="newSupplierEmail">
                        </div>
                        <div class="col-md-4">
                            <label class="form-label">Discount %</label>
                            <input type="number" class="form-control" id="newSupplierDiscount"
                                   min="0" max="100" step="0.01" value="0">
                        </div>
                        <div class="col-md-4">
                            <label class="form-label">&nbsp;</label>
                            <div>
                                <button id="saveSupplierBtn" class="btn btn-success">
                                    <i class="bi bi-check-lg me-1"></i> Save Supplier
                                </button>
                                <button id="cancelSupplierBtn" class="btn btn-secondary ms-2">
                                    <i class="bi bi-x me-1"></i> Cancel
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        `;

        $('#supplierCreationContainer').html(formHtml);
        $('#supplierCreationSection').show();
        $('#proceedToMaterials').show();

        const self = this;

        // Save supplier button
        $('#saveSupplierBtn').on('click', function() {
            self.createSupplier(callback);
        });

        // Cancel supplier button
        $('#cancelSupplierBtn').on('click', function() {
            $('#supplierCreationSection').hide();
            // Reset the dropdown if it was set to "new"
            if (self.currentSupplierSelect && self.currentSupplierSelect.val() === 'new') {
                self.currentSupplierSelect.val(''); // Reset to empty
                self.currentSupplierSelect = null;
            }
        });
    }

    createSupplier(callback) {
        const supplierData = {
            name: $('#newSupplierName').val().trim(),
            contact_person: $('#newSupplierContact').val().trim(),
            phone: $('#newSupplierPhone').val().trim(),
            email: $('#newSupplierEmail').val().trim(),
            discount_percentage: $('#newSupplierDiscount').val() || 0
        };

        // Validate
        if (!supplierData.name) {
            alert('Supplier name is required');
            return;
        }

        // Show loading state
        $('#saveSupplierBtn').prop('disabled', true).html('<span class="spinner-border spinner-border-sm me-1"></span> Creating...');

        $.post('/api/recipe_import/create_supplier', supplierData)
            .done((response) => {
                if (response.success) {
                    // Add to local list
                    this.newSuppliers.push(response.supplier_id);

                    // Show success message
                    const successHtml = `
                        <div class="alert alert-success">
                            <i class="bi bi-check-circle me-2"></i>
                            Supplier "${response.supplier_name}" created successfully!
                        </div>
                    `;
                    $('#supplierCreationContainer').html(successHtml);

                    // Reload suppliers list
                    this.loadSuppliers();

                    // Call callback if provided
                    if (callback) {
                        callback(response.supplier_id);
                    }
                } else {
                    alert('Error: ' + response.error);
                    $('#saveSupplierBtn').prop('disabled', false).html('<i class="bi bi-check-lg me-1"></i> Save Supplier');
                }
            })
            .fail((error) => {
                alert('Failed to create supplier');
                $('#saveSupplierBtn').prop('disabled', false).html('<i class="bi bi-check-lg me-1"></i> Save Supplier');
            });
    }

    createMaterial($row, $btn) {
        // Get form data from the row
        const materialData = {
            name: $row.data('material-name'),
            category_id: $row.find('.category-select').val(),
            unit: $row.find('.unit-select').val(),
            supplier_id: $row.find('.supplier-select').val(),
            sku: $row.find('.sku-input').val().trim(),
            price: $row.find('.price-input').val()
        };

        // Validate
        if (!materialData.category_id) {
            alert('Please select a category');
            return;
        }
        if (!materialData.supplier_id) {
            alert('Please select a supplier');
            return;
        }
        if (materialData.supplier_id === 'new') {
            alert('Please create the new supplier first by selecting "New Supplier" from the dropdown');
            return;
        }
        if (!materialData.sku) {
            alert('SKU is required');
            return;
        }

        // Show loading state
        $btn.prop('disabled', true).html('<span class="spinner-border spinner-border-sm me-1"></span> Creating...');

        const recipeIdx = $row.data('recipe-idx');
        const materialIdx = $row.data('material-idx');
        const key = `${recipeIdx}_${materialIdx}`;

        $.post('/api/recipe_import/create_material', materialData)
            .done((response) => {
                if (response.success) {
                    // Update material state
                    this.materialStates.set(key, 'created');
                    this.createdMaterials[key] = response.material_id;

                    // Update UI
                    $row.removeClass('table-danger').addClass('table-success');

                    // Update status cell
                    const $statusCell = $row.find('.material-status-cell');
                    $statusCell.html(`
                        <span class="badge bg-success">
                            <i class="bi bi-check-circle"></i> Created
                        </span>
                    `);

                    // Hide creation form
                    $row.find('.material-create-form').slideUp(200);
                    $row.find('.expand-create-btn i').removeClass('bi-chevron-up').addClass('bi-chevron-down');

                    // Update the mapping dropdown if it exists
                    const $mappingSelect = $row.find('.material-mapping-select');
                    if ($mappingSelect.length) {
                        // Add new option to dropdown
                        $mappingSelect.append(`<option value="${response.material_id}" selected>${response.material_name}</option>`);
                        $mappingSelect.val(response.material_id);
                        $mappingSelect.trigger('change');
                    }

                    // Update all mapping dropdowns for same material type
                    $('.material-mapping-select').each((index, select) => {
                        const $select = $(select);
                        if ($select.data('material-type') === $row.data('material-type')) {
                            if ($select.find(`option[value="${response.material_id}"]`).length === 0) {
                                $select.append(`<option value="${response.material_id}">${response.material_name}</option>`);
                            }
                        }
                    });

                    this.validateAndProceed();
                } else {
                    alert('Error: ' + response.error);
                    $btn.prop('disabled', false).html('<i class="bi bi-check-lg me-1"></i> Create');
                }
            })
            .fail((error) => {
                alert('Failed to create material');
                $btn.prop('disabled', false).html('<i class="bi bi-check-lg me-1"></i> Create');
            });
    }

    validateAndProceed() {
        // Check if all required materials are mapped or created
        let allMapped = true;
        let unmappedCount = 0;

        $('.recipe-select-checkbox:checked').each((index, checkbox) => {
            const recipeIdx = $(checkbox).data('recipe-idx');

            // Check materials in this recipe
            $(`.material-row[data-recipe-idx="${recipeIdx}"]`).each((idx, row) => {
                const $row = $(row);
                const materialIdx = $row.data('material-idx');
                const key = `${recipeIdx}_${materialIdx}`;
                const state = this.materialStates.get(key);

                if (state === 'unmapped') {
                    const $mappingSelect = $row.find('.material-mapping-select');
                    if ($mappingSelect.length && !$mappingSelect.val()) {
                        allMapped = false;
                        unmappedCount++;
                    }
                }
            });
        });

        // Update submit button state
        const $submitBtn = $('#confirmImportBtn');
        if (allMapped) {
            $submitBtn.prop('disabled', false)
                     .removeClass('btn-secondary')
                     .addClass('btn-success');
        } else {
            $submitBtn.prop('disabled', true)
                     .removeClass('btn-success')
                     .addClass('btn-secondary');
        }
    }
}

// Initialize when document is ready
$(document).ready(function() {
    // Only initialize on recipe review page
    if ($('#recipeImportForm').length > 0) {
        window.recipeImportCreator = new RecipeImportMaterialCreator();
    }
});