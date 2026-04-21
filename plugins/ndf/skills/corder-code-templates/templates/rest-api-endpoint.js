/**
 * REST API Endpoint Template
 *
 * このテンプレートは、Express.jsを使用したRESTful APIエンドポイントの実装例です。
 * CRUD操作、バリデーション、エラーハンドリング、認証が含まれています。
 *
 * 使用方法:
 * 1. [RESOURCE] を実際のリソース名に置換（例: users, products, orders）
 * 2. Model を実際のデータベースモデルに置換
 * 3. バリデーションルールをビジネスロジックに合わせて調整
 * 4. 認証が必要なエンドポイントに authMiddleware を追加
 */

const express = require('express');
const router = express.Router();
const { body, param, query, validationResult } = require('express-validator');

// 認証ミドルウェア（必要に応じてインポート）
// const { authMiddleware, requireRole } = require('../middleware/auth');

// モデル（実際のモデルに置換）
// const Model = require('../models/[RESOURCE]');

/**
 * @route   GET /api/[resource]
 * @desc    Get all [resource] items with pagination and filtering
 * @access  Public
 */
router.get(
  '/',
  [
    query('page').optional().isInt({ min: 1 }).toInt(),
    query('limit').optional().isInt({ min: 1, max: 100 }).toInt(),
    query('sortBy').optional().isString(),
    query('order').optional().isIn(['asc', 'desc'])
  ],
  async (req, res) => {
    try {
      // バリデーションエラーチェック
      const errors = validationResult(req);
      if (!errors.isEmpty()) {
        return res.status(400).json({
          success: false,
          errors: errors.array()
        });
      }

      // ページネーション設定
      const page = req.query.page || 1;
      const limit = req.query.limit || 10;
      const offset = (page - 1) * limit;

      // ソート設定
      const sortBy = req.query.sortBy || 'createdAt';
      const order = req.query.order || 'desc';

      // データ取得（実際のクエリに置換）
      // const { count, rows } = await Model.findAndCountAll({
      //   limit,
      //   offset,
      //   order: [[sortBy, order.toUpperCase()]]
      // });

      // サンプルレスポンス（実際のデータに置換）
      const count = 0;
      const rows = [];

      res.json({
        success: true,
        data: rows,
        pagination: {
          total: count,
          page,
          limit,
          totalPages: Math.ceil(count / limit)
        }
      });
    } catch (error) {
      console.error('Error fetching [resource]:', error);
      res.status(500).json({
        success: false,
        error: 'Internal server error',
        message: process.env.NODE_ENV === 'development' ? error.message : undefined
      });
    }
  }
);

/**
 * @route   GET /api/[resource]/:id
 * @desc    Get single [resource] item by ID
 * @access  Public
 */
router.get(
  '/:id',
  [
    param('id').isInt().toInt()
  ],
  async (req, res) => {
    try {
      const errors = validationResult(req);
      if (!errors.isEmpty()) {
        return res.status(400).json({
          success: false,
          errors: errors.array()
        });
      }

      const { id } = req.params;

      // データ取得（実際のクエリに置換）
      // const item = await Model.findByPk(id);

      const item = null; // サンプル

      if (!item) {
        return res.status(404).json({
          success: false,
          error: '[Resource] not found'
        });
      }

      res.json({
        success: true,
        data: item
      });
    } catch (error) {
      console.error(`Error fetching [resource] ${req.params.id}:`, error);
      res.status(500).json({
        success: false,
        error: 'Internal server error'
      });
    }
  }
);

/**
 * @route   POST /api/[resource]
 * @desc    Create new [resource] item
 * @access  Private (認証必要な場合は authMiddleware 追加)
 */
router.post(
  '/',
  // authMiddleware, // 認証が必要な場合はコメント解除
  [
    body('name').trim().notEmpty().withMessage('Name is required')
      .isLength({ min: 2, max: 100 }).withMessage('Name must be 2-100 characters'),
    body('email').optional().isEmail().withMessage('Invalid email format'),
    body('status').optional().isIn(['active', 'inactive']).withMessage('Invalid status')
    // 必要に応じてバリデーションルールを追加
  ],
  async (req, res) => {
    try {
      const errors = validationResult(req);
      if (!errors.isEmpty()) {
        return res.status(400).json({
          success: false,
          errors: errors.array()
        });
      }

      // データ作成（実際のモデルに置換）
      // const newItem = await Model.create(req.body);

      const newItem = { id: 1, ...req.body }; // サンプル

      res.status(201).json({
        success: true,
        data: newItem,
        message: '[Resource] created successfully'
      });
    } catch (error) {
      console.error('Error creating [resource]:', error);

      // ユニーク制約違反等のエラーハンドリング
      if (error.name === 'SequelizeUniqueConstraintError') {
        return res.status(409).json({
          success: false,
          error: 'Resource already exists',
          field: error.errors[0]?.path
        });
      }

      res.status(500).json({
        success: false,
        error: 'Internal server error'
      });
    }
  }
);

/**
 * @route   PUT /api/[resource]/:id
 * @desc    Update [resource] item by ID
 * @access  Private
 */
router.put(
  '/:id',
  // authMiddleware,
  [
    param('id').isInt().toInt(),
    body('name').optional().trim().isLength({ min: 2, max: 100 }),
    body('email').optional().isEmail(),
    body('status').optional().isIn(['active', 'inactive'])
  ],
  async (req, res) => {
    try {
      const errors = validationResult(req);
      if (!errors.isEmpty()) {
        return res.status(400).json({
          success: false,
          errors: errors.array()
        });
      }

      const { id } = req.params;

      // データ取得と更新（実際のクエリに置換）
      // const item = await Model.findByPk(id);
      // if (!item) {
      //   return res.status(404).json({
      //     success: false,
      //     error: '[Resource] not found'
      //   });
      // }
      //
      // await item.update(req.body);

      const item = null; // サンプル

      if (!item) {
        return res.status(404).json({
          success: false,
          error: '[Resource] not found'
        });
      }

      res.json({
        success: true,
        data: item,
        message: '[Resource] updated successfully'
      });
    } catch (error) {
      console.error(`Error updating [resource] ${req.params.id}:`, error);
      res.status(500).json({
        success: false,
        error: 'Internal server error'
      });
    }
  }
);

/**
 * @route   DELETE /api/[resource]/:id
 * @desc    Delete [resource] item by ID
 * @access  Private (Admin only)
 */
router.delete(
  '/:id',
  // authMiddleware,
  // requireRole('admin'), // 管理者のみ削除可能な場合
  [
    param('id').isInt().toInt()
  ],
  async (req, res) => {
    try {
      const errors = validationResult(req);
      if (!errors.isEmpty()) {
        return res.status(400).json({
          success: false,
          errors: errors.array()
        });
      }

      const { id } = req.params;

      // データ取得と削除（実際のクエリに置換）
      // const item = await Model.findByPk(id);
      // if (!item) {
      //   return res.status(404).json({
      //     success: false,
      //     error: '[Resource] not found'
      //   });
      // }
      //
      // await item.destroy();

      const item = null; // サンプル

      if (!item) {
        return res.status(404).json({
          success: false,
          error: '[Resource] not found'
        });
      }

      res.json({
        success: true,
        message: '[Resource] deleted successfully'
      });
    } catch (error) {
      console.error(`Error deleting [resource] ${req.params.id}:`, error);
      res.status(500).json({
        success: false,
        error: 'Internal server error'
      });
    }
  }
);

/**
 * @route   GET /api/[resource]/search
 * @desc    Search [resource] items
 * @access  Public
 */
router.get(
  '/search',
  [
    query('q').trim().notEmpty().withMessage('Search query is required'),
    query('limit').optional().isInt({ min: 1, max: 100 }).toInt()
  ],
  async (req, res) => {
    try {
      const errors = validationResult(req);
      if (!errors.isEmpty()) {
        return res.status(400).json({
          success: false,
          errors: errors.array()
        });
      }

      const { q, limit = 10 } = req.query;

      // 検索クエリ（実際のクエリに置換）
      // const results = await Model.findAll({
      //   where: {
      //     name: {
      //       [Op.like]: `%${q}%`
      //     }
      //   },
      //   limit
      // });

      const results = []; // サンプル

      res.json({
        success: true,
        data: results,
        query: q
      });
    } catch (error) {
      console.error('Error searching [resource]:', error);
      res.status(500).json({
        success: false,
        error: 'Internal server error'
      });
    }
  }
);

module.exports = router;
