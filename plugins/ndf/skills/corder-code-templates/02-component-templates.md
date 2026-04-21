# コンポーネント・モデル テンプレート

## React Component

```jsx
// components/[ComponentName].jsx
import React, { useState, useEffect } from 'react';
import PropTypes from 'prop-types';

const ComponentName = ({ initialData, onUpdate }) => {
  const [data, setData] = useState(initialData || []);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetchData();
  }, []);

  const fetchData = async () => {
    try {
      setLoading(true);
      const response = await fetch('/api/resource');
      const result = await response.json();

      if (!result.success) {
        throw new Error(result.error);
      }

      setData(result.data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = async (formData) => {
    try {
      const response = await fetch('/api/resource', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(formData)
      });
      const result = await response.json();

      if (result.success) {
        setData([...data, result.data]);
        onUpdate?.(result.data);
      }
    } catch (err) {
      setError(err.message);
    }
  };

  if (loading) return <div className="loading">Loading...</div>;
  if (error) return <div className="error">Error: {error}</div>;

  return (
    <div className="component-name">
      {/* Component content */}
      <ul>
        {data.map(item => (
          <li key={item.id}>{item.name}</li>
        ))}
      </ul>
    </div>
  );
};

ComponentName.propTypes = {
  initialData: PropTypes.array,
  onUpdate: PropTypes.func
};

ComponentName.defaultProps = {
  initialData: [],
  onUpdate: null
};

export default ComponentName;
```

## React Component (TypeScript)

```tsx
// components/[ComponentName].tsx
import React, { useState, useEffect, FC } from 'react';

interface Item {
  id: number;
  name: string;
}

interface Props {
  initialData?: Item[];
  onUpdate?: (item: Item) => void;
}

const ComponentName: FC<Props> = ({ initialData = [], onUpdate }) => {
  const [data, setData] = useState<Item[]>(initialData);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchData();
  }, []);

  const fetchData = async () => {
    try {
      setLoading(true);
      const response = await fetch('/api/resource');
      const result = await response.json();
      setData(result.data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  };

  if (loading) return <div>Loading...</div>;
  if (error) return <div>Error: {error}</div>;

  return (
    <ul>
      {data.map(item => (
        <li key={item.id}>{item.name}</li>
      ))}
    </ul>
  );
};

export default ComponentName;
```

## Database Model (Sequelize)

```javascript
// models/[ModelName].js
const { DataTypes } = require('sequelize');

module.exports = (sequelize) => {
  const ModelName = sequelize.define('ModelName', {
    id: {
      type: DataTypes.INTEGER,
      primaryKey: true,
      autoIncrement: true
    },
    name: {
      type: DataTypes.STRING,
      allowNull: false,
      validate: {
        notEmpty: true,
        len: [2, 100]
      }
    },
    email: {
      type: DataTypes.STRING,
      unique: true,
      allowNull: false,
      validate: {
        isEmail: true
      }
    },
    status: {
      type: DataTypes.ENUM('active', 'inactive', 'pending'),
      defaultValue: 'pending'
    },
    createdAt: {
      type: DataTypes.DATE,
      defaultValue: DataTypes.NOW
    }
  }, {
    tableName: 'model_names',
    timestamps: true
  });

  // Associations
  ModelName.associate = (models) => {
    ModelName.hasMany(models.RelatedModel, {
      foreignKey: 'modelNameId',
      as: 'relatedModels'
    });
  };

  // Hooks
  ModelName.beforeCreate(async (instance) => {
    // Pre-processing logic
  });

  return ModelName;
};
```

## Database Model (Mongoose)

```javascript
// models/[ModelName].js
const mongoose = require('mongoose');

const modelNameSchema = new mongoose.Schema({
  name: {
    type: String,
    required: [true, 'Name is required'],
    trim: true,
    minlength: 2,
    maxlength: 100
  },
  email: {
    type: String,
    required: true,
    unique: true,
    lowercase: true,
    match: [/^\S+@\S+\.\S+$/, 'Invalid email']
  },
  status: {
    type: String,
    enum: ['active', 'inactive', 'pending'],
    default: 'pending'
  }
}, {
  timestamps: true
});

// Index
modelNameSchema.index({ email: 1 });

// Virtual
modelNameSchema.virtual('displayName').get(function() {
  return this.name.toUpperCase();
});

// Instance method
modelNameSchema.methods.isActive = function() {
  return this.status === 'active';
};

// Static method
modelNameSchema.statics.findByEmail = function(email) {
  return this.findOne({ email });
};

module.exports = mongoose.model('ModelName', modelNameSchema);
```
